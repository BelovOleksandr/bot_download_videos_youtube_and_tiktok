import asyncio
import logging
import re
import uuid
import time
import math
from pathlib import Path
from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import Message, FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.config import settings
from app.downloader import download_video

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("tgbot")

URL_RE = re.compile(r"(https?://[^\s]+)", re.I)

# Словарь для хранения информации о загруженных видео
video_cache = {}

# Для очистки старых файлов
CLEANUP_INTERVAL = 3600  # 1 час
LAST_CLEANUP = time.time()


def detect_source(url: str) -> str:
    u = url.lower()
    if "tiktok.com" in u:
        return "tiktok"
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    return "other"


def create_cut_keyboard(video_id: str) -> InlineKeyboardMarkup:
    """Создает клавиатуру с кнопками для нарезки видео"""
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(text="Нарезать по 15 сек", callback_data=f"slice:{video_id}:15"),
        InlineKeyboardButton(text="Нарезать по 30 сек", callback_data=f"slice:{video_id}:30"),
    )
    builder.row(
        InlineKeyboardButton(text="Нарезать по 45 сек", callback_data=f"slice:{video_id}:45"),
        InlineKeyboardButton(text="Нарезать по 60 сек", callback_data=f"slice:{video_id}:60"),
    )
    builder.row(
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"cancel:{video_id}")
    )

    return builder.as_markup()


async def cleanup_old_files():
    """Удаляет файлы старше 1 часа"""
    global LAST_CLEANUP
    now = time.time()

    if now - LAST_CLEANUP < CLEANUP_INTERVAL:
        return

    download_dir = settings.DOWNLOAD_DIR
    for file in download_dir.glob("*_part_*.mp4"):
        if now - file.stat().st_mtime > CLEANUP_INTERVAL:
            try:
                file.unlink()
                log.info(f"Удален старый файл: {file.name}")
            except Exception as e:
                log.error(f"Ошибка при удалении {file.name}: {e}")

    LAST_CLEANUP = now


async def get_video_duration(video_path: str) -> float:
    """Получает длительность видео через ffprobe"""
    ffprobe_path = r'C:\Users\semen\AppData\Local\Microsoft\WinGet\Links\ffprobe.exe'

    cmd = [
        ffprobe_path,
        '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        video_path
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        raise Exception(f"Ошибка получения длительности: {stderr.decode()}")

    return float(stdout.decode().strip())


async def slice_video(video_path: str, segment_duration: int) -> list:
    """
    Нарезает видео на сегменты указанной длительности
    Возвращает список путей к созданным файлам
    """
    # Получаем длительность видео
    duration = await get_video_duration(video_path)

    # Вычисляем количество сегментов
    num_segments = math.ceil(duration / segment_duration)

    original_path = Path(video_path)
    created_files = []

    ffmpeg_path = r'C:\Users\semen\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe'

    for i in range(num_segments):
        start_time = i * segment_duration
        segment_filename = f"{original_path.stem}_part_{i + 1}_{segment_duration}s{original_path.suffix}"
        segment_path = original_path.parent / segment_filename

        # Команда для нарезки сегмента
        cmd = [
            ffmpeg_path,
            '-i', video_path,
            '-ss', str(start_time),
            '-t', str(segment_duration),
            '-c', 'copy',
            '-avoid_negative_ts', 'make_zero',
            '-y',
            str(segment_path)
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            log.error(f"FFmpeg error for segment {i + 1}: {stderr.decode()}")
            # Продолжаем с другими сегментами даже если один не удался
            continue

        created_files.append(str(segment_path))

    return created_files


async def start_handler(msg: Message):
    await msg.answer(
        "Привет! Пришли ссылку на видео YouTube/TikTok — я скачаю и пришлю файл. "
        "После загрузки ты сможешь **нарезать видео** на равные части по 15, 30, 45 или 60 секунд.\n\n"
        "Большие файлы (>50 МБ):\n• обычный режим — дам ссылку на скачивание\n"
        "• с локальным Bot API — отправлю прямо в чат до 2 ГБ.\n\n"
        "Подсказка: можно кидать сразу несколько ссылок (по одной в сообщении)."
    )


async def url_handler(msg: Message):
    m = URL_RE.search(msg.text or "")
    if not m:
        return
    url = m.group(1)
    src = detect_source(url)
    status = await msg.reply(f"Скачиваю из {src}… Подождите ⏳")

    try:
        res = await asyncio.get_running_loop().run_in_executor(
            None, download_video, url, settings.DOWNLOAD_DIR
        )
    except Exception as e:
        log.exception("download error")
        await status.edit_text(f"Не удалось скачать: {e}")
        return

    # Генерируем уникальный ID для видео
    video_id = str(uuid.uuid4())[:8]

    # Сохраняем информацию о видео
    video_cache[video_id] = {
        'path': str(res.filepath),
        'title': res.title,
        'filesize': res.filesize
    }

    using_local_api = bool(settings.LOCAL_BOT_API_URL)
    try:
        caption = f"{res.title}\nРазмер: {res.filesize / 1024 / 1024:.1f} МБ\n\nВыбери длительность сегментов для нарезки:"

        if using_local_api or res.filesize <= settings.MAX_STD_API_BYTES:
            file = FSInputFile(res.filepath)

            if res.filepath.suffix.lower() in {".mp4", ".mov", ".m4v"}:
                await msg.answer_video(
                    video=file,
                    caption=caption,
                    reply_markup=create_cut_keyboard(video_id)
                )
            else:
                await msg.answer_document(
                    document=file,
                    caption=caption,
                    reply_markup=create_cut_keyboard(video_id)
                )
            await status.delete()
        else:
            base = settings.EXTERNAL_BASE_URL
            if not base:
                await status.edit_text(
                    "Файл большой (>50 МБ). Включите локальный Bot API "
                    "или задайте EXTERNAL_BASE_URL для ссылки на скачивание."
                )
                return
            link = f"{base}/files/{res.filepath.name}"
            await status.edit_text(
                f"Файл большой (>50 МБ). Забрать здесь:\n{link}\n\n"
                f"{caption}\n(Совет: включите локальный Bot API, чтобы получать файл прямо в чат)"
            )
    except Exception as e:
        log.exception("send error")
        await status.edit_text(f"Ошибка при отправке: {e}")


async def slice_callback_handler(callback: CallbackQuery):
    """Обработчик нажатий на кнопки нарезки"""
    # Очищаем старые файлы
    await cleanup_old_files()

    data = callback.data.split(':')
    action = data[0]
    video_id = data[1]

    if action == "cancel":
        await callback.message.delete()
        await callback.answer("Отменено")
        return

    if action == "slice":
        segment_duration = int(data[2])

        if video_id not in video_cache:
            await callback.answer("Видео не найдено в кэше. Возможно, прошло слишком много времени.", show_alert=True)
            return

        video_info = video_cache[video_id]
        video_path = video_info['path']

        # Показываем, что обрабатываем
        await callback.message.edit_caption(
            caption=f"✂️ Нарезаю видео на сегменты по {segment_duration} секунд... (это может занять некоторое время)"
        )

        try:
            # Получаем длительность видео
            duration = await get_video_duration(video_path)

            # Нарезаем видео
            segment_paths = await slice_video(video_path, segment_duration)

            if not segment_paths:
                await callback.message.edit_caption(
                    caption="❌ Не удалось создать ни одного сегмента."
                )
                await callback.answer("Ошибка", show_alert=True)
                return

            # Удаляем сообщение с кнопками
            await callback.message.delete()

            # Отправляем информацию о нарезке
            await callback.message.answer(
                f"✅ Видео нарезано на {len(segment_paths)} сегментов по {segment_duration} секунд\n"
                f"Длительность исходного видео: {duration:.1f} секунд\n"
                f"Отправляю сегменты..."
            )

            # Отправляем каждый сегмент
            for i, segment_path in enumerate(segment_paths):
                segment_file = FSInputFile(segment_path)
                segment_size = Path(segment_path).stat().st_size

                segment_caption = f"{video_info['title']}\n"
                segment_caption += f"📽️ Сегмент {i + 1} из {len(segment_paths)}\n"
                segment_caption += f"⏱️ Длительность: {segment_duration} сек\n"
                segment_caption += f"📊 Размер: {segment_size / 1024 / 1024:.1f} МБ"

                await callback.message.answer_video(
                    video=segment_file,
                    caption=segment_caption
                )

                # Небольшая задержка между отправками
                await asyncio.sleep(0.5)

            await callback.answer(f"✅ Отправлено {len(segment_paths)} сегментов")

        except Exception as e:
            log.exception("slice error")
            await callback.message.edit_caption(
                caption=f"❌ Ошибка при нарезке: {e}\n\nПопробуйте еще раз или выберите другую длительность."
            )
            await callback.answer("Ошибка", show_alert=True)


# ---------- Локальный HTTP-сервер для раздачи файлов (fallback) ----------

async def file_handler(request: web.Request):
    name = request.match_info["name"]
    path = settings.DOWNLOAD_DIR / name
    if not path.exists():
        return web.Response(status=404, text="Not found")
    return web.FileResponse(path)


def make_web_app() -> web.Application:
    app = web.Application()
    app.add_routes([web.get("/files/{name}", file_handler)])
    return app


async def main():
    if not settings.BOT_TOKEN:
        raise SystemExit("BOT_TOKEN не задан в .env")

    session = None
    if settings.LOCAL_BOT_API_URL:
        api = TelegramAPIServer.from_base(settings.LOCAL_BOT_API_URL.rstrip("/"))
        session = AiohttpSession(api=api)

    bot = Bot(
        token=settings.BOT_TOKEN,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    # Регистрируем обработчики
    dp.message.register(start_handler, CommandStart())
    dp.message.register(url_handler, F.text.regexp(URL_RE))
    dp.callback_query.register(slice_callback_handler, F.data.startswith(("slice:", "cancel:")))

    # Создаем папку для загрузок если её нет
    settings.DOWNLOAD_DIR.mkdir(exist_ok=True)

    # Запускаем файловый сервер
    web_app = make_web_app()
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", settings.FILE_SERVER_PORT)
    await site.start()
    log.info(f"File server on http://127.0.0.1:{settings.FILE_SERVER_PORT}/files/<name>")

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())