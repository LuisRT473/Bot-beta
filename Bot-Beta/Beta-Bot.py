# chatbot_telegram_con_csv_mejorado (reusable start/end).py
# ✅ Reinicio de conversación (con /start o /reset)
# ✅ Corrige bugs (coincidencias, referencias)
# ✅ /help y /cancel
# ✅ Mantiene funciones base y guardado en CSV
# ✅ Sin periféricos
# ✅ Sin "tipo de cuenta" en alta de correo
# ✅ Flujo impresora (cable/Wi-Fi)
# ✅ Flujo correo (problema): Búsqueda de antiguos / Envío y recibo
# ✅ Ticket con Zona y Departamento

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)
from sentence_transformers import SentenceTransformer, util
import csv
import os
import re
import unicodedata

# =====================
# CONFIGURACIÓN
# =====================
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8221975234:AAGBa58JEzvZuGxK3cIM9O3Tr51k4QzNv_4")

# Estados para manejar la conversación (24 en total)
(
    PREGUNTA,
    CONFIRMAR,
    OTRO_PROBLEMA,
    PREGUNTAR_CONEXION,
    PREGUNTAR_REVISION,
    CONFIRMAR_TICKET,
    TICKET_NOMBRE,
    TICKET_CORREO,
    TICKET_ZONA,
    TICKET_DEPTO,
    TICKET_DESC,
    TICKET_PRIORIDAD,
    # --- Correo nuevo ingreso ---
    C_NOMBRE,
    C_APELLIDO,
    C_AREA,
    C_PUESTO,
    C_JEFE,
    C_FECHA_INGRESO,
    C_CONTACTO,
    C_CONFIRMAR,
    # --- Impresora ---
    IMP_TIPO,
    IMP_CONFIRMA,
    # --- Correo problemas (nuevo flujo) ---
    COR_TIPO,
    COR_CONFIRMA,
) = range(24)

# 1. Cargar modelo de embeddings
modelo = SentenceTransformer('all-MiniLM-L6-v2')

# 2. Documentación (manuales o guías)
documentos = {
    "correo": (
        "CONFIGURACIÓN DE CORREO ELECTRÓNICO EN OUTLOOK\n"
        "- Abrir Outlook\n"
        "- Ir a Archivo > Agregar cuenta\n"
        "- Ingresar correo y contraseña\n\n"
        "PROBLEMAS CON LA BÚSQUEDA DE CORREOS:\n"
        "- Cierra Outlook\n"
        "- Ve a Panel de control > Opciones de indización\n"
        "- Asegúrate de que Microsoft Outlook aparezca en la lista de ubicaciones indexadas\n"
        "- Si no está, haz clic en Modificar y márcalo\n"
        "- Haz clic en Opciones avanzadas > Reconstruir el índice\n"
        "- Este proceso puede tardar según la cantidad de correos.\n"
    ),
    "red": (
        "CONFIGURACIÓN DE RED\n"
        "- Abrir Panel de control > Redes\n"
        "- Configurar adaptador\n"
        "- Verificar dirección IP"
    ),
    "impresora": (
        "CONFIGURACIÓN DE IMPRESORA\n"
        "- Verificar el cable de red/USB\n"
        "- Probar desconectar y volver a conectar el cable\n"
        "- Comprobar que el driver esté instalado"
    ),
    "equipo": (
        "CONFIGURACIÓN DE EQUIPO DE CÓMPUTO\n"
        "- Cierra las ventanas que no estés utilizando\n"
        "- Prueba reiniciar el equipo"
    ),
}

# 3. Embeddings
docs_keys = list(documentos.keys())
docs_embeddings = modelo.encode(list(documentos.values()), convert_to_tensor=True)

# =====================
# UTILIDADES
# =====================

def _strip_accents(s: str) -> str:
    s = s or ""
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")

def _n(txt: str) -> str:
    return (txt or "").strip()

def _nl(txt: str) -> str:
    return _strip_accents((txt or "").strip()).lower()

def es_correo_valido(correo: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", _n(correo)))

def guardar_csv(ruta: str, encabezados: list, fila: list):
    existe = os.path.isfile(ruta)
    with open(ruta, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not existe:
            w.writerow(encabezados)
        w.writerow(fila)

def guardar_ticket(nombre, correo, zona, departamento, descripcion, prioridad):
    guardar_csv(
        "tickets.csv",
        ["Nombre", "Correo", "Zona", "Departamento", "Descripción", "Prioridad"],
        [nombre, correo, zona, departamento, descripcion, prioridad],
    )

# =====================
# LÓGICA DE RESPUESTAS
# =====================

PATRONES_CORREO_ALTA = [
    r"\b(nuevo|nueva)\s+(correo|email|cuenta)\b",
    r"\b(crear|creacion|creacion de|crear cuenta|provisionar|alta|dar de alta)\b.*\b(correo|email|cuenta)\b",
    r"\b(correo|email|cuenta)\b.*\b(nuevo|nueva|ingreso|crear|creacion|alta)\b",
    r"\b(nuevo ingreso|alta de (usuario|colaborador))\b.*\b(correo|email|cuenta)\b",
    r"\b(crear|provisionar)\s+(correo|email)\b",
    r"\b(correo|email)\s+(para|de)\s+(nuevo ingreso|nuevo colaborador|nuevo usuario)\b",
]

def arbol_decision(pregunta: str):
    p = _nl(pregunta)

    # Alta/creación de correo (prioridad sobre genérico de correo)
    if any(re.search(pat, p) for pat in PATRONES_CORREO_ALTA):
        return "flujo_correo"

    # Red / sin internet
    if any(w in p for w in ["no tengo red","sin red","no hay internet","internet","red"]):
        return "no_red"

    # Impresora (flujo interactivo)
    if any(w in p for w in ["impresora","printer"]):
        return "flujo_impresora"

    # Correo genérico -> flujo interactivo de problemas
    if any(w in p for w in ["problemas","correo","outlook","email","e-mail"]):
        return "flujo_correo_problemas"

    # Equipo lento / congelado
    if any(w in p for w in ["equipo","computadora","pc","lento","se congela"]):
        return (
            "👉 ¿El equipo no responde o está lento?\n"
            "- Cierra las ventanas que no estés utilizando.\n"
            "- Prueba reiniciar el equipo."
        )

    return None

def responder(pregunta: str):
    respuesta_arbol = arbol_decision(pregunta)
    if respuesta_arbol:
        return respuesta_arbol

    pregunta_emb = modelo.encode(pregunta, convert_to_tensor=True)
    similitudes = util.cos_sim(pregunta_emb, docs_embeddings)
    idx_max = int(similitudes.squeeze(0).argmax().item())
    score = float(similitudes.squeeze(0)[idx_max].item())

    if score > 0.5:
        clave = docs_keys[idx_max]
        return f"📄 Basado en la documentación de {clave.capitalize()}:\n\n{documentos[clave]}"

    return None

# =====================
# HANDLERS BASE
# =====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "🤖 Hola, soy Beta-Bot, tu asistente de soporte TI.\n"
        "Cuéntame tu problema"
    )
    return PREGUNTA

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Comandos disponibles:\n"
        "/start – Iniciar o reiniciar la asistencia.\n"
        "/reset – Reinicia el flujo desde cero.\n"
        "/cancel – Cancela la conversación actual.\n"
        "/nuevo_correo – Alta de correo para nuevo ingreso."
    )

async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("🔄 Flujo reiniciado. ¿Cuál es tu problema?")
    return PREGUNTA

async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("🚪 Conversación cancelada. Escribe /start cuando quieras volver a comenzar.")
    return ConversationHandler.END

# =====================
# FLUJO BASE (DIAGNÓSTICO + TICKET)
# =====================

async def manejar_pregunta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pregunta = _n(update.message.text)
    r = responder(pregunta)

    if r == "flujo_correo":
        return await correo_start(update, context)

    if r == "no_red":
        await update.message.reply_text("👉 ¿Tu conexión es por *cable* o *wifi*?", parse_mode="Markdown")
        return PREGUNTAR_CONEXION

    if r == "flujo_impresora":
        return await impresora_start(update, context)

    if r == "flujo_correo_problemas":
        return await correo_prob_start(update, context)

    if r:
        context.user_data["ultima_respuesta"] = r
        await update.message.reply_text(f"{r}\n\n🤖 ¿Se solucionó tu problema? (si/no)")
        return CONFIRMAR

    await update.message.reply_text("🤖 No encontré solución en mis manuales. ¿Quieres levantar un ticket? (si/no)")
    return CONFIRMAR_TICKET

async def preguntar_conexion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    respuesta = _nl(update.message.text)
    if "cable" in respuesta:
        await update.message.reply_text("🔌 Revisa que el cable esté bien conectado. ¿Ya lo revisaste? (si/no)")
        return PREGUNTAR_REVISION
    if "wifi" in respuesta:
        await update.message.reply_text("📡 Verifica que el Wi-Fi esté encendido y conectado. ¿Ya lo probaste? (si/no)")
        return PREGUNTAR_REVISION
    await update.message.reply_text("Responde *cable* o *wifi*.", parse_mode="Markdown")
    return PREGUNTAR_CONEXION

async def preguntar_revision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = _nl(update.message.text)
    if t == "si":
        # ✅ Pasos de entorno empresarial, sin reiniciar módem
        await update.message.reply_text(
            "🛠️ Si aún falla, prueba lo siguiente:\n" 
            " A) Deshabilita y habilita el *adaptador de red* (Panel de control > Centro de redes → Cambiar configuración del adaptador).\n" 
            " B) Prueba *otro puerto o cable*\n"
            "🤖 ¿Se solucionó? (si/no)",
            parse_mode="Markdown"
        )
        return CONFIRMAR
    if t == "no":
        await update.message.reply_text(
            "👉 Realiza primero las verificaciones indicadas. Si persiste, puedo **levantar un ticket** para redes. ¿Deseas hacerlo? (si/no)"
        )
        return CONFIRMAR_TICKET
    await update.message.reply_text("Responde 'si' o 'no'.")
    return PREGUNTAR_REVISION


async def confirmar_solucion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = _nl(update.message.text)
    if t == "si":
        await update.message.reply_text("🤖 ¿Tienes *otro* problema? (si/no)", parse_mode="Markdown")
        return OTRO_PROBLEMA
    if t == "no":
        await update.message.reply_text("¿Deseas levantar un ticket? (si/no)")
        return CONFIRMAR_TICKET
    await update.message.reply_text("Responde 'si' o 'no'.")
    return CONFIRMAR

async def confirmar_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = _nl(update.message.text)
    if t == "si":
        await update.message.reply_text("👤 Tu *nombre*:", parse_mode="Markdown")
        return TICKET_NOMBRE
    if t == "no":
        await update.message.reply_text("De acuerdo. Dime tu siguiente problema o usa /nuevo_correo.")
        return PREGUNTA
    await update.message.reply_text("Responde 'si' o 'no'.")
    return CONFIRMAR_TICKET

async def otro_problema(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = _nl(update.message.text)
    if t == "si":
        await update.message.reply_text("Cuéntame tu siguiente problema:")
        return PREGUNTA
    if t == "no":
        await update.message.reply_text("👋 ¡Listo! Escribe /start cuando quieras volver a iniciar.")
        return ConversationHandler.END
    await update.message.reply_text("Responde 'si' o 'no'.")
    return OTRO_PROBLEMA

# ===== TICKET =====
async def ticket_nombre(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["nombre"] = _n(update.message.text)
    await update.message.reply_text("📧 Ingresa tu *correo*:", parse_mode="Markdown")
    return TICKET_CORREO

async def ticket_correo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    correo = _n(update.message.text)
    if not es_correo_valido(correo):
        await update.message.reply_text("⚠️ El formato del correo no parece válido. Intenta de nuevo, por favor.")
        return TICKET_CORREO
    context.user_data["correo"] = correo
    await update.message.reply_text("🌎 Indica tu *zona* (ej.: Norte, Centro, Sur):", parse_mode="Markdown")
    return TICKET_ZONA

async def ticket_zona(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["zona"] = _n(update.message.text)
    await update.message.reply_text("🏢 Indica tu *departamento* (ej.: Ventas, TI, Finanzas):", parse_mode="Markdown")
    return TICKET_DEPTO

async def ticket_depto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["departamento"] = _n(update.message.text)
    await update.message.reply_text("📝 Describe tu *problema*:", parse_mode="Markdown")
    return TICKET_DESC

async def ticket_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["descripcion"] = _n(update.message.text)
    await update.message.reply_text("⚡ Prioridad (*baja*/*media*/*alta*):", parse_mode="Markdown")
    return TICKET_PRIORIDAD

async def ticket_prioridad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["prioridad"] = _nl(update.message.text)

    guardar_ticket(
        context.user_data.get("nombre", ""),
        context.user_data.get("correo", ""),
        context.user_data.get("zona", ""),
        context.user_data.get("departamento", ""),
        context.user_data.get("descripcion", ""),
        context.user_data.get("prioridad", ""),
    )

    await update.message.reply_text(
        "✅ Ticket generado y guardado.\n\n"
        f"- Nombre: {context.user_data['nombre']}\n"
        f"- Correo: {context.user_data['correo']}\n"
        f"- Zona: {context.user_data['zona']}\n"
        f"- Departamento: {context.user_data['departamento']}\n"
        f"- Descripción: {context.user_data['descripcion']}\n"
        f"- Prioridad: {context.user_data['prioridad']}"
    )
    await update.message.reply_text(
        "🚪 Cerrando la sesión. Gracias por contactarme.\n"
        "🔁 Si necesitas más ayuda, escribe /start para iniciar una nueva sesión o /reset para reiniciar."
    )
    context.user_data.clear()
    return ConversationHandler.END

# =====================
# FLUJO: IMPRESORA (INTERACTIVO)
# =====================
async def impresora_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🖨️ ¿Tu impresora está conectada por *cable* o *wifi*?", parse_mode="Markdown")
    return IMP_TIPO

async def impresora_tipo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = _nl(update.message.text)
    if "cable" in t:
        context.user_data["imp_tipo"] = "cable"
        await update.message.reply_text(
            "🔌 Pasos para impresora por **cable**:\n"
            "1) Verifica el cable USB/Red y prueba otro puerto.\n"
            "2) En Windows: Panel de control → Dispositivos e impresoras → Agregar impresora.\n"
            "3) Reinstala/actualiza el driver del fabricante.\n"
            "4) Imprime página de prueba.\n\n"
            "🤖 ¿Se solucionó? (si/no)",
            parse_mode="Markdown"
        )
        return IMP_CONFIRMA

    if "wifi" in t or "wi-fi" in t:
        context.user_data["imp_tipo"] = "wifi"
        await update.message.reply_text(
            "📡 Pasos para impresora por **Wi-Fi**:\n"
            "1) Asegúrate de que la impresora y la PC estén en la **misma red**.\n"
            "2) Comprueba la IP de la impresora (pantalla o página de configuración).\n"
            "3) En Windows: Agregar impresora → \"La impresora no está en la lista\" → "
            "\"Agregar por dirección TCP/IP\" y pon la IP.\n"
            "4) Reinstala/actualiza el driver si no la detecta.\n\n"
            "🤖 ¿Se solucionó? (si/no)",
            parse_mode="Markdown"
        )
        return IMP_CONFIRMA

    await update.message.reply_text("Responde *cable* o *wifi*.", parse_mode="Markdown")
    return IMP_TIPO

async def impresora_confirmar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = _nl(update.message.text)
    if t == "si":
        await update.message.reply_text("✅ ¡Excelente! ¿Tienes *otro* problema? (si/no)", parse_mode="Markdown")
        return OTRO_PROBLEMA
    if t == "no":
        tip_extra = ""
        if context.user_data.get("imp_tipo") == "wifi":
            tip_extra = (
                "\n🔎 Extra: Verifica que el puerto no esté bloqueado por firewall y que la IP no cambie (reserva DHCP)."
            )
        elif context.user_data.get("imp_tipo") == "cable":
            tip_extra = (
                "\n🔎 Extra: Prueba otro cable/puerto, y revisa en 'Colas de impresión' si hay trabajos atascados."
            )

        await update.message.reply_text(
            "😕 Entendido. Puedo ayudarte a **levantar un ticket** para seguimiento." + tip_extra +
            "\n\n¿Deseas levantar un ticket? (si/no)"
        )
        return CONFIRMAR_TICKET

    await update.message.reply_text("Responde 'si' o 'no'.")
    return IMP_CONFIRMA

# =====================
# FLUJO: CORREO (PROBLEMAS)
# =====================
async def correo_prob_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✉️ Problemas de correo.\n"
        "¿Qué tipo de problema presentas?\n"
        "- *Búsqueda de correos antiguos*\n"
        "- *Envío y recibo de correos*\n\n"
        "Escribe una de las dos opciones.",
        parse_mode="Markdown"
    )
    return COR_TIPO

async def correo_prob_tipo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = _nl(update.message.text)
    context.user_data["correo_prob"] = None

    if "busqueda" in t or "antigu" in t or "viejo" in t or "antiguos" in t:
        context.user_data["correo_prob"] = "busqueda"
        await update.message.reply_text(
            "🔎 **Búsqueda de correos antiguos** – Pasos recomendados:\n"
            "1) Cierra Outlook.\n"
            "2) Panel de control → *Opciones de indización*.\n"
            "3) Asegúrate de que **Microsoft Outlook** esté en *Ubicaciones indexadas* (botón *Modificar*).\n"
            "4) *Opciones avanzadas* → *Reconstruir índice* (puede tardar según la cantidad de correos).\n"
            "5) En Outlook: *Archivo → Opciones → Buscar* y verifica el ámbito y filtros.\n\n"
            "🤖 ¿Se solucionó? (si/no)",
            parse_mode="Markdown"
        )
        return COR_CONFIRMA

    if "envio" in t or "envío" in t or "recibo" in t or "enviar" in t or "recibir" in t:
        context.user_data["correo_prob"] = "envio_recibo"
        await update.message.reply_text(
            "📤 **Envío y recibo de correos** – Pasos recomendados:\n"
            "1) Verifica usuario y contraseña.\n"
            "2) Revisa espacio disponible del buzón (cuota) y carpeta *Bandeja de salida*.\n"
            "3) Configura servidores:\n"
            "   - SMTP (salida): puerto 587 TLS (o 465 SSL según proveedor).\n"
            "   - IMAP (entrada): puerto 993 SSL/TLS (o POP3 995).\n"
            "4) Prueba desactivar temporalmente antivirus/firewall para descartar bloqueo de puertos.\n"
            "5) Comprueba conectividad a los hosts SMTP/IMAP (ping o telnet a puertos).\n\n"
            "🤖 ¿Se solucionó? (si/no)",
            parse_mode="Markdown"
        )
        return COR_CONFIRMA

    await update.message.reply_text(
        "Por favor escribe exactamente una de estas opciones:\n"
        "- *Búsqueda de correos antiguos*\n"
        "- *Envío y recibo de correos*",
        parse_mode="Markdown"
    )
    return COR_TIPO

async def correo_prob_confirmar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = _nl(update.message.text)
    if t == "si":
        await update.message.reply_text("✅ ¡Excelente! ¿Tienes *otro* problema? (si/no)", parse_mode="Markdown")
        return OTRO_PROBLEMA
    if t == "no":
        extra = ""
        if context.user_data.get("correo_prob") == "envio_recibo":
            extra = (
                "\n🔎 Extra: Revisa que la autenticación SMTP esté habilitada y que no haya bloqueos por SPF/DKIM/DMARC."
            )
        elif context.user_data.get("correo_prob") == "busqueda":
            extra = (
                "\n🔎 Extra: En Outlook, reconstruye el archivo OST y verifica que el modo caché esté habilitado."
            )

        await update.message.reply_text(
            "😕 Entendido. Puedo ayudarte a **levantar un ticket** para seguimiento." + extra +
            "\n\n¿Deseas levantar un ticket? (si/no)"
        )
        return CONFIRMAR_TICKET

    await update.message.reply_text("Responde 'si' o 'no'.")
    return COR_CONFIRMA

# =====================
# FLUJO: CORREO PARA NUEVO INGRESO (ALTA)
# =====================
async def correo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await correo_start(update, context)

async def correo_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["alta"] = {}
    await update.message.reply_text("✉️ Alta de correo para nuevo ingreso.\nNombre(s) del nuevo colaborador:")
    return C_NOMBRE

async def correo_nombre(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["alta"]["nombres"] = _n(update.message.text)
    await update.message.reply_text("Apellido(s):")
    return C_APELLIDO

async def correo_apellido(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["alta"]["apellidos"] = _n(update.message.text)
    await update.message.reply_text("Área/Departamento:")
    return C_AREA

async def correo_area(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["alta"]["area"] = _n(update.message.text)
    await update.message.reply_text("Puesto:")
    return C_PUESTO

async def correo_puesto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["alta"]["puesto"] = _n(update.message.text)
    await update.message.reply_text("Jefe directo (nombre):")
    return C_JEFE

async def correo_jefe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["alta"]["jefe"] = _n(update.message.text)
    await update.message.reply_text("Fecha de ingreso (YYYY-MM-DD):")
    return C_FECHA_INGRESO

async def correo_fecha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["alta"]["fecha_ingreso"] = _n(update.message.text)
    await update.message.reply_text("Correo alterno o teléfono de contacto (para enviar credenciales):")
    return C_CONTACTO

async def correo_contacto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["alta"]["contacto"] = _n(update.message.text)
    a = context.user_data["alta"]
    await update.message.reply_text(
        "¿Confirmas la solicitud de creación de correo? (si/no)\n"
        f"- Nombre: {a.get('nombres','')} {a.get('apellidos','')}\n"
        f"- Área: {a.get('area','')} | Puesto: {a.get('puesto','')}\n"
        f"- Jefe: {a.get('jefe','')} | Ingreso: {a.get('fecha_ingreso','')}\n"
        f"- Contacto: {a.get('contacto','')}\n"
    )
    return C_CONFIRMAR

async def correo_confirmar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = _nl(update.message.text)
    if t == "si":
        a = context.user_data.get("alta", {})
        guardar_csv(
            "solicitudes_correos.csv",
            ["Nombres", "Apellidos", "Área", "Puesto", "Jefe", "FechaIngreso", "Contacto"],
            [a.get("nombres", ""), a.get("apellidos", ""), a.get("area", ""), a.get("puesto", ""), a.get("jefe", ""),
             a.get("fecha_ingreso", ""), a.get("contacto", "")],
        )
        await update.message.reply_text("✅ Solicitud de creación de correo registrada. ¡Gracias!")
        await update.message.reply_text("🚪 Sesión cerrada. Usa /start para nueva sesión.")
        context.user_data.clear()
        return ConversationHandler.END
    if t == "no":
        await update.message.reply_text("❌ Solicitud cancelada. Puedes iniciar otra con /nuevo_correo.")
        return PREGUNTA
    await update.message.reply_text("Responde 'si' o 'no'.")
    return C_CONFIRMAR

# =====================
# MAIN
# =====================

def main():
    app = Application.builder().token(TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("reset", reset_cmd),
            CommandHandler("nuevo_correo", correo_cmd),
        ],
        states={
            # Base
            PREGUNTA: [MessageHandler(filters.TEXT & ~filters.COMMAND, manejar_pregunta)],
            PREGUNTAR_CONEXION: [MessageHandler(filters.TEXT & ~filters.COMMAND, preguntar_conexion)],
            PREGUNTAR_REVISION: [MessageHandler(filters.TEXT & ~filters.COMMAND, preguntar_revision)],
            CONFIRMAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirmar_solucion)],
            CONFIRMAR_TICKET: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirmar_ticket)],
            OTRO_PROBLEMA: [MessageHandler(filters.TEXT & ~filters.COMMAND, otro_problema)],
            # Ticket
            TICKET_NOMBRE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ticket_nombre)],
            TICKET_CORREO: [MessageHandler(filters.TEXT & ~filters.COMMAND, ticket_correo)],
            TICKET_ZONA: [MessageHandler(filters.TEXT & ~filters.COMMAND, ticket_zona)],
            TICKET_DEPTO: [MessageHandler(filters.TEXT & ~filters.COMMAND, ticket_depto)],
            TICKET_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, ticket_desc)],
            TICKET_PRIORIDAD: [MessageHandler(filters.TEXT & ~filters.COMMAND, ticket_prioridad)],
            # Correo nuevo ingreso (alta)
            C_NOMBRE: [MessageHandler(filters.TEXT & ~filters.COMMAND, correo_nombre)],
            C_APELLIDO: [MessageHandler(filters.TEXT & ~filters.COMMAND, correo_apellido)],
            C_AREA: [MessageHandler(filters.TEXT & ~filters.COMMAND, correo_area)],
            C_PUESTO: [MessageHandler(filters.TEXT & ~filters.COMMAND, correo_puesto)],
            C_JEFE: [MessageHandler(filters.TEXT & ~filters.COMMAND, correo_jefe)],
            C_FECHA_INGRESO: [MessageHandler(filters.TEXT & ~filters.COMMAND, correo_fecha)],
            C_CONTACTO: [MessageHandler(filters.TEXT & ~filters.COMMAND, correo_contacto)],
            C_CONFIRMAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, correo_confirmar)],
            # Impresora
            IMP_TIPO: [MessageHandler(filters.TEXT & ~filters.COMMAND, impresora_tipo)],
            IMP_CONFIRMA: [MessageHandler(filters.TEXT & ~filters.COMMAND, impresora_confirmar)],
            # Correo problemas
            COR_TIPO: [MessageHandler(filters.TEXT & ~filters.COMMAND, correo_prob_tipo)],
            COR_CONFIRMA: [MessageHandler(filters.TEXT & ~filters.COMMAND, correo_prob_confirmar)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_cmd),
            CommandHandler("help", help_cmd),
        ],
        allow_reentry=True,
    )

    app.add_handler(conv)

    print("🤖 Bot corriendo en Telegram...")
    app.run_polling()

if __name__ == "__main__":
    main()
