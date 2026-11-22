import asyncio
import logging
import os
import sys

from telegram.ext import ApplicationBuilder

from cleanup import cleanup_loop
from handlers import register_handlers

logging.basicConfig(
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def wait_for_bot_api(url: str, max_wait: int = 60):
    """Wait for the self-hosted Bot API server to become reachable."""
    import httpx
    for i in range(max_wait):
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, timeout=2)
                logger.info("Bot API server ready (attempt %d)", i + 1)
                return
        except Exception:
            if i % 5 == 0:
                logger.info("Waiting for Bot API server at %s... (attempt %d)", url, i + 1)
            await asyncio.sleep(1)
    logger.warning("Bot API server not reachable after %ds, starting anyway", max_wait)


def main():
    bot_token = os.environ.get("BOT_TOKEN")
    if not bot_token:
        logger.error("BOT_TOKEN environment variable is required")
        sys.exit(1)

    # Heroku uses Telegram's official Bot API by default (50 MB upload limit).
    # A custom/local Bot API server is used only when explicitly configured.
    api_url = os.environ.get("TELEGRAM_BOT_API_URL", "").strip()
    builder = (
        ApplicationBuilder()
        .token(bot_token)
        .read_timeout(60)
        .write_timeout(60)
        .connect_timeout(30)
    )

    if api_url:
        base_url = f"{api_url.rstrip('/')}/bot"
        base_file_url = f"{api_url.rstrip('/')}/file/bot"
        logger.info("Starting Afrodit with custom Telegram Bot API: %s", api_url)
        asyncio.get_event_loop().run_until_complete(wait_for_bot_api(api_url))
        builder = builder.base_url(base_url).base_file_url(base_file_url)
    else:
        logger.info("Starting Afrodit with the official Telegram Bot API")

    app = builder.build()

    register_handlers(app)

    async def post_init(application):
        asyncio.create_task(cleanup_loop())
        from telegram import BotCommand
        await application.bot.set_my_commands([
            BotCommand("start", "Afrodit'i başlat"),
            BotCommand("help", "Yardım ve komutlar"),
            BotCommand("mp3", "Bağlantıyı MP3 olarak indir"),
            BotCommand("mp4", "Bağlantıyı MP4 olarak indir"),
            BotCommand("best", "En iyi kaliteyle indir"),
            BotCommand("platforms", "Desteklenen platformlar"),
            BotCommand("settings", "İndirme tercihlerin"),
            BotCommand("setquality", "Varsayılan video kalitesini ayarla"),
            BotCommand("setformat", "Varsayılan indirme biçimini ayarla"),
            BotCommand("stats", "Bot istatistikleri"),
        ])
        # Set the Telegram display name and profile text to Afrodit.
        try:
            await application.bot.set_my_name("Afrodit")
            await application.bot.set_my_description(
                "Instagram, TikTok, Pinterest, YouTube, X ve Reddit bağlantılarını otomatik indirir. Bağlantıyı sohbete gönderin."
            )
            await application.bot.set_my_short_description(
                "Afrodit — otomatik medya indirici"
            )
        except Exception:
            logger.exception("Could not update the Telegram bot profile; set it manually via @BotFather")
        logger.info("Afrodit commands registered")

    app.post_init = post_init
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
