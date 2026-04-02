import asyncio
from collections.abc import Callable
import io

from google.genai.live import AsyncSession
from mss import mss
from mss.exception import ScreenShotError
from PIL import Image

from .live_config import ScreenShareConfig


def capture_screen_frame(
    monitor: dict[str, int],
    screen_share_config: ScreenShareConfig,
) -> bytes:
    with mss() as screen_capture:
        screenshot = screen_capture.grab(monitor)
    screenshot_image = Image.frombytes(
        "RGB",
        screenshot.size,
        screenshot.rgb,
    )
    buffer = io.BytesIO()
    screenshot_image.save(
        buffer,
        format="JPEG",
        quality=screen_share_config.jpeg_quality,
    )
    return buffer.getvalue()


async def share_screen(
    session: AsyncSession,
    screen_monitor: dict[str, int],
    screen_share_config: ScreenShareConfig,
    reconnect_error: Callable[[str], Exception],
) -> None:
    """Captures the screen and streams it to the Live API at a low frame rate."""
    warned_screen_share_error = False
    while True:
        try:
            frame = await asyncio.to_thread(
                capture_screen_frame,
                screen_monitor,
                screen_share_config,
            )
        except (OSError, ScreenShotError) as exc:
            if not warned_screen_share_error:
                from .console import console_output

                print(console_output.screen_share_disabled(exc))
                warned_screen_share_error = True
            return
        if frame:
            try:
                await session.send_realtime_input(
                    video={
                        "data": frame,
                        "mime_type": screen_share_config.mime_type,
                    }
                )
            except Exception as exc:
                raise reconnect_error(f"Screen share disconnected: {exc}") from exc
        await asyncio.sleep(1 / screen_share_config.fps)
