# chatbot_telegram_con_csv_mejorado (reusable start/end).py
# ✅ Listo para reiniciar la conversación al terminar (con /start o /reset)
# ✅ Corrige pequeños bugs de tu versión (diccionario, flujos, coincidencias)
# ✅ Añade /help y /cancel
# ✅ Mantiene todas tus funciones base y el guardado en CSV

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

# =====================
# CONFIGURACIÓN
# =====================
# Recomendado: exporta TOKEN en variables de entorno (evita exponerlo en código)
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8221975234:AAGBa58JEzvZuGxK3cIM9O3Tr51k4QzNv_4")

# Estados para manejar la conversación
(
    PREGUNTA,
    CONFIRMAR,
    OTRO_PROBLEMA,
    PREGUNTAR_CONEXION,
    PREGUNTAR_REVISION,
    CONFIRMAR_TICKET,
    TICKET_NOMBRE,
    TICKET_CORREO,
    TICKET_DESC,
    TICKET_PRIORIDAD,
) = range(10)

# 1. Cargar modelo de embeddings
modelo = SentenceTransformer('all-MiniLM-L6-v2')

# 2. Documentación (manuales o guías)
documentos = {
    "correo": (
        "CONFIGURACIÓN DE CORREO ELECTRÓNICO EN OUTLOOK\n"
        "- Abrir Outlook\n"
        "- Ir a Archivo > Agregar cuenta\n"
        "- Ingresar correo y contraseña"
    ),
    "red": (
        "CONFIGURACIÓN DE RED\n"
        "- Abrir Panel de control > Redes\n"
        "- Configurar adaptador\n"
        "- Verificar dirección IP"
    ),
    "impresora": (
        "CONFIGURACIÓN DE IMPRESORA\n"
        "- Verificar el cable de red\n"
        "- Probar desconectar y volver a conectar el cable\n"
        "- Comprobar que el driver esté instalado"
    ),
    "equipo": (
        "CONFIGURACIÓN DE EQUIPO DE CÓMPUTO\n"
        "- Cierra las ventanas que no estés utilizando\n"
        "- Prueba reiniciar el equipo"
    ),
}

# 3. Crear embeddings de los documentos
docs_keys = list(documentos.keys())
docs_embeddings = modelo.encode(list(documentos.values()), convert_to_tensor=True)


# =====================
# FUNCIONES DEL BOT
# =====================

def _normaliza(texto: str) -> str:
    return (texto or "").strip().lower()


def arbol_decision(pregunta: str):
    p = _normaliza(pregunta)
    if any(w in p for w in ["no tengo red", "sin red", "no hay internet", "internet", "red"]):
        return "no_red"   # caso especial: activa flujo de diagnóstico
    elif any(w in p for w in ["correo", "outlook", "email", "e-mail"]):
        return (
            "👉 Verifica que el correo y la contraseña sean correctos.\n"
            "👉 Si no puedes enviar, revisa configuración SMTP.\n"
            "👉 Si no puedes recibir, revisa IMAP/POP3 y el espacio en el buzón."
        )
    elif any(w in p for w in ["impresora", "printer"]):
        return (
            "👉 ¿La impresora está conectada por cable o WiFi?\n"
            "- Cable: revisa el USB/Red y reinstala driver.\n"
            "- WiFi: asegúrate de que esté en la misma red que tu PC."
        )
    elif any(w in p for w in ["equipo", "computadora", "pc", "lento", "se congela"]):
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
    similitudes = util.cos_sim(pregunta_emb, docs_embeddings)  # shape (1, N)
    idx_max = int(similitudes.squeeze(0).argmax().item())
    score = float(similitudes.squeeze(0)[idx_max].item())

    if score > 0.5:
        clave = docs_keys[idx_max]
        return f"📄 Basado en la documentación de {clave.capitalize()}:\n\n{documentos[clave]}"

    return None


def guardar_ticket(nombre, correo, descripcion, prioridad):
    archivo = "tickets.csv"
    existe = os.path.isfile(archivo)
    with open(archivo, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not existe:
            writer.writerow(["Nombre", "Correo", "Descripción", "Prioridad"])
        writer.writerow([nombre, correo, descripcion, prioridad])


def es_correo_valido(correo: str) -> bool:
    patron = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
    return bool(re.match(patron, (correo or "").strip()))


# =====================
# HANDLERS DE TELEGRAM
# =====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Limpiamos cualquier residuo de una conversación anterior
    context.user_data.clear()
    await update.message.reply_text(
        "🤖 Hola, soy Beta‑Bot, tu asistente de soporte TI.\n"
        "Cuéntame tu problema o escribe /help para ver opciones."
    )
    return PREGUNTA


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Comandos disponibles:\n"
        "/start – Iniciar o reiniciar la asistencia.\n"
        "/reset – Reinicia el flujo desde cero.\n"
        "/cancel – Cancela la conversación actual."
    )


async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("🔄 Flujo reiniciado. ¿Cuál es tu problema?")
    return PREGUNTA


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("🚪 Conversación cancelada. Puedes escribir /start cuando quieras volver a comenzar.")
    return ConversationHandler.END


async def manejar_pregunta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pregunta = (update.message.text or "").strip()
    respuesta = responder(pregunta)

    if respuesta == "no_red":
        await update.message.reply_text("👉 ¿Tu conexión es por *cable* o *wifi*?", parse_mode="Markdown")
        return PREGUNTAR_CONEXION

    if respuesta:
        context.user_data["ultima_respuesta"] = respuesta
        await update.message.reply_text(f"{respuesta}\n\n🤖 ¿Se solucionó tu problema? (si/no)")
        return CONFIRMAR
    else:
        await update.message.reply_text("🤖 No encontré una solución en mis manuales. ¿Quieres levantar un ticket? (si/no)")
        return CONFIRMAR_TICKET


# === Flujo específico para "no tengo red" ===
async def preguntar_conexion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    respuesta = _normaliza(update.message.text)
    if "cable" in respuesta:
        await update.message.reply_text(
            "🔌 Verifica que el cable esté conectado correctamente.\n¿Ya lo revisaste? (si/no)"
        )
        return PREGUNTAR_REVISION
    elif "wifi" in respuesta:
        await update.message.reply_text(
            "📡 Revisa que tu WiFi esté encendido y conectado.\n¿Ya lo probaste? (si/no)"
        )
        return PREGUNTAR_REVISION
    else:
        await update.message.reply_text("Por favor responde *cable* o *wifi*.", parse_mode="Markdown")
        return PREGUNTAR_CONEXION


async def preguntar_revision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    respuesta = _normaliza(update.message.text)
    if respuesta == "si":
        await update.message.reply_text(
            "✅ Perfecto. Si aún no funciona, intenta reiniciar el módem.\n\n🤖 ¿Se solucionó tu problema? (si/no)"
        )
        return CONFIRMAR
    elif respuesta == "no":
        await update.message.reply_text(
            "👉 Revisa primero la conexión y dime si se solucionó.\n🤖 ¿Quieres que espere mientras lo verificas? (si/no)"
        )
        return CONFIRMAR
    else:
        await update.message.reply_text("Por favor responde 'si' o 'no'.")
        return PREGUNTAR_REVISION


async def confirmar_solucion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = _normaliza(update.message.text)
    if texto == "si":
        await update.message.reply_text("🤖 ¿Tienes *otro* problema que quieras revisar? (si/no)", parse_mode="Markdown")
        return OTRO_PROBLEMA
    elif texto == "no":
        await update.message.reply_text("🤖 Entendido. Puedo ayudarte a levantar un ticket para seguimiento. ¿Deseas hacerlo? (si/no)")
        return CONFIRMAR_TICKET
    else:
        await update.message.reply_text("Por favor responde 'si' o 'no'.")
        return CONFIRMAR


async def confirmar_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = _normaliza(update.message.text)
    if texto == "si":
        await update.message.reply_text("👤 Por favor, dime tu *nombre*:", parse_mode="Markdown")
        return TICKET_NOMBRE
    elif texto == "no":
        await update.message.reply_text("👍 De acuerdo. Si necesitas algo más, dime tu siguiente problema o usa /cancel para salir.")
        return PREGUNTA
    else:
        await update.message.reply_text("Por favor responde 'si' o 'no'.")
        return CONFIRMAR_TICKET


async def otro_problema(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = _normaliza(update.message.text)
    if texto == "si":
        await update.message.reply_text("👍 De acuerdo, dime tu siguiente problema:")
        return PREGUNTA
    elif texto == "no":
        await update.message.reply_text("👋 ¡Perfecto! Me alegra haberte ayudado. Escribe /start si quieres iniciar de nuevo.")
        return ConversationHandler.END
    else:
        await update.message.reply_text("Por favor responde 'si' o 'no'.")
        return OTRO_PROBLEMA


# ===== TICKET =====
async def ticket_nombre(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["nombre"] = (update.message.text or "").strip()
    await update.message.reply_text("📧 Ingresa tu *correo*:", parse_mode="Markdown")
    return TICKET_CORREO


async def ticket_correo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    correo = (update.message.text or "").strip()
    if not es_correo_valido(correo):
        await update.message.reply_text("⚠️ El formato del correo no parece válido. Intenta de nuevo, por favor.")
        return TICKET_CORREO
    context.user_data["correo"] = correo
    await update.message.reply_text("📝 Describe tu *problema*:", parse_mode="Markdown")
    return TICKET_DESC


async def ticket_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["descripcion"] = (update.message.text or "").strip()
    await update.message.reply_text("⚡ Prioridad (*baja*/*media*/*alta*):", parse_mode="Markdown")
    return TICKET_PRIORIDAD


async def ticket_prioridad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["prioridad"] = (update.message.text or "").strip().lower()

    guardar_ticket(
        context.user_data.get("nombre", ""),
        context.user_data.get("correo", ""),
        context.user_data.get("descripcion", ""),
        context.user_data.get("prioridad", ""),
    )

    await update.message.reply_text(
        "✅ Ticket generado y guardado.\n\n"
        f"- Nombre: {context.user_data['nombre']}\n"
        f"- Correo: {context.user_data['correo']}\n"
        f"- Descripción: {context.user_data['descripcion']}\n"
        f"- Prioridad: {context.user_data['prioridad']}"
    )
    await update.message.reply_text(
        "🚪 Cerrando la sesión. Gracias por contactarme.\n"
        "🔁 Si necesitas más ayuda, escribe /start para iniciar una nueva sesión o /reset para reiniciar."
    )
    # Limpiamos estado y cerramos conversación
    context.user_data.clear()
    return ConversationHandler.END


# =====================
# MAIN
# =====================

def main():
    app = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start), CommandHandler("reset", reset_cmd)],
        states={
            PREGUNTA: [MessageHandler(filters.TEXT & ~filters.COMMAND, manejar_pregunta)],
            PREGUNTAR_CONEXION: [MessageHandler(filters.TEXT & ~filters.COMMAND, preguntar_conexion)],
            PREGUNTAR_REVISION: [MessageHandler(filters.TEXT & ~filters.COMMAND, preguntar_revision)],
            CONFIRMAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirmar_solucion)],
            CONFIRMAR_TICKET: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirmar_ticket)],
            OTRO_PROBLEMA: [MessageHandler(filters.TEXT & ~filters.COMMAND, otro_problema)],
            TICKET_NOMBRE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ticket_nombre)],
            TICKET_CORREO: [MessageHandler(filters.TEXT & ~filters.COMMAND, ticket_correo)],
            TICKET_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, ticket_desc)],
            TICKET_PRIORIDAD: [MessageHandler(filters.TEXT & ~filters.COMMAND, ticket_prioridad)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_cmd),
            CommandHandler("help", help_cmd),
        ],
        allow_reentry=True,  # ✅ Permite re-entrar al handler tras finalizar
    )

    app.add_handler(conv_handler)

    print("🤖 Bot corriendo en Telegram...")
    app.run_polling()


if __name__ == "__main__":
    main()
