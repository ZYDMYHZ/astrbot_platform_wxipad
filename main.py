from astrbot.api.star import Context, Star, register
from astrbot.api.event import AstrMessageEvent

@register("wxipad", "ZYDMYHZ", "微信个人号平台适配器", "1.0.0")
class WechatPlatformPlugin(Star):
    def __init__(self, context: Context):
        from .wechat_adapter import WechatPlatformAdapter  # noqa 