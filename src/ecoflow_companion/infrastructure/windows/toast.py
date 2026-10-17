"""Windows toast notifications for an unpackaged desktop app.

Windows shows toasts only for a known AppUserModelID. An unpackaged app registers one under
``HKCU\\Software\\Classes\\AppUserModelId`` (display name and icon) and gives its process that
ID; no Start-menu shortcut or admin rights are needed. Clicking a toast does not start the app.
"""

import contextlib
import ctypes
import logging
from pathlib import Path
from xml.sax.saxutils import escape

from ecoflow_companion.application.notification_service import DeliveryResult, Notification

log = logging.getLogger(__name__)

APP_ID = "EcoFlowCompanion.Desktop"
APP_NAME = "EcoFlow Companion"
APP_ID_KEY = rf"Software\Classes\AppUserModelId\{APP_ID}"
GROUP = "ecoflow"


def register_app_id(icon: Path | None) -> None:
    """Register the AppUserModelID for this user and use it for this process. Raises OSError."""
    import winreg  # Windows only

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, APP_ID_KEY) as key:
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
        if icon is not None:
            winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, str(icon))
    shell32 = ctypes.WinDLL("shell32")
    shell32.SetCurrentProcessExplicitAppUserModelID.argtypes = (ctypes.c_wchar_p,)
    hresult = shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    if hresult != 0:
        raise OSError(f"SetCurrentProcessExplicitAppUserModelID failed: {hresult:#x}")


def unregister_app_id() -> None:
    import winreg

    with contextlib.suppress(FileNotFoundError):
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, APP_ID_KEY)


def toast_xml(notification: Notification) -> str:
    return (
        '<toast><visual><binding template="ToastGeneric">'
        f"<text>{escape(notification.title)}</text>"
        f"<text>{escape(notification.body)}</text>"
        "</binding></visual></toast>"
    )


class WinRtToastNotifier:
    """Toasts through ``Windows.UI.Notifications``. Construction raises if WinRT is missing."""

    def __init__(self, app_id: str = APP_ID) -> None:
        from winrt.windows.data.xml.dom import XmlDocument
        from winrt.windows.ui.notifications import (
            NotificationSetting,
            ToastNotification,
            ToastNotificationManager,
        )

        self._xml_document = XmlDocument
        self._toast = ToastNotification
        self._enabled = NotificationSetting.ENABLED
        self._notifier = ToastNotificationManager.create_toast_notifier_with_id(app_id)

    def setting_name(self) -> str | None:
        """Windows' notification setting for this app; None when Windows cannot say.

        Windows answers "element not found" for an app ID that has no Start-menu shortcut,
        even though such an app can show toasts.
        """
        try:
            return self._notifier.setting.name.lower()
        except OSError:
            return None

    def notify(self, notification: Notification) -> DeliveryResult:
        setting = self.setting_name()
        if setting is not None and setting != self._enabled.name.lower():
            # The user or a policy turned notifications off for this app: not delivered
            return DeliveryResult(False, f"blocked by Windows ({setting})")
        document = self._xml_document()
        document.load_xml(toast_xml(notification))
        toast = self._toast(document)
        toast.tag = notification.category
        toast.group = GROUP
        title = notification.title
        toast.add_failed(
            lambda _sender, args: log.warning(
                "Windows failed to show '%s': %s", title, getattr(args, "error_code", "?")
            )
        )
        try:
            self._notifier.show(toast)
        except OSError as e:
            return DeliveryResult(False, f"Windows refused the toast ({e})")
        if setting is None:
            return DeliveryResult(True, "accepted by Windows; its on/off setting is unknown")
        return DeliveryResult(True, "accepted by Windows")
