"""只检查本地 QQ 邮箱配置，不连接服务器、不发送邮件。"""
import sys
from notification_core import MailSettings
from settings import MAIL_CONFIG

if __name__ == "__main__":
    try:
        settings = MailSettings.load(MAIL_CONFIG)
    except Exception as exc:
        print(f"邮件配置有误（{type(exc).__name__}），请核对 mail.ini.example 中的字段。")
        sys.exit(1)
    print("配置检查通过；邮件已启用。尚未验证真实投递。" if settings.enabled else
          "邮件尚未启用；网页告警不受影响。请在 mail.ini 填写邮箱及授权码，再设置 enabled=true。")
