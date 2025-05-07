import asyncio
import time
import sys

from astrbot.api.platform import Platform, AstrBotMessage, MessageMember, PlatformMetadata, MessageType
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain, Image, Video, Record
from astrbot.core.platform.astr_message_event import MessageSesion
from astrbot.api.platform import register_platform_adapter
from astrbot import logger
from .client import ipad855Client

if sys.version_info >= (3, 12):
    from typing import override
else:
    from typing_extensions import override

@register_platform_adapter("wxipad", "ipad微信适配器", default_config_tmpl={
    "ws_url": "ws://192.168.5.2:8848/ws/GetSyncMsg",
    "token": "",
    "host": "192.168.5.2",
    "port": 8848,
    "wxid": "机器人微信id",
    "redbag_enabled": False, # 是否开启红包助手
    "redbag_interval": 0 # 领取红包延迟，单位秒
})
class WechatPlatformAdapter(Platform):
    def __init__(self, platform_config: dict, platform_settings: dict, event_queue: asyncio.Queue) -> None:
        super().__init__(event_queue)
        self.config = platform_config
        self.settings = platform_settings
        self.token = platform_config.get("token")
        self.ws_url = platform_config.get("ws_url")
        self.host = self.config.get("host")
        self.port = self.config.get("port")
        self.redbag_enabled = platform_config.get("redbag_enabled", False)
        self.redbag_interval = platform_config.get("redbag_interval", 0)
        self.ws = None
        self._is_terminating = False
        
        # 获取机器人wxid
        self.wxid = platform_config.get("wxid", "")
        if not self.wxid:
            logger.error("未配置机器人wxid，请检查配置文件")
            raise ValueError("未配置机器人wxid")
        
        # 初始化 client
        self.client = ipad855Client(
            host=self.host,
            port=self.port,
            token=self.token,
            ws_url=self.ws_url,
            event_queue=event_queue
        )

    @override
    async def send_by_session(
        self, session: MessageSesion, message_chain: MessageChain
    ):
        session_id = session.session_id
        if "#" in session_id:
            # unique session
            to_wxid = session_id.split("#")[1]
        else:
            to_wxid = session_id

        await WechatPlatformAdapter.send_with_client(
            message_chain, to_wxid, self.client
        )

        await super().send_by_session(session, message_chain)

    def meta(self) -> PlatformMetadata:
        return PlatformMetadata(
            "wxipad",
            "ipad微信适配器"
        )

    async def run(self):
        """启动适配器"""

        async def on_received(data):
            # logger.debug(f"收到消息: {data}\n")
            abm = await self.is_text_message(data=data) # 转换成 AstrBotMessage
            if abm is None:
                logger.debug("消息转换结果为None，忽略处理")
                return
            await self.handle_msg(abm)

        self.client.on_message_received = on_received
        is_online = await self.client.check_and_login()
        if not is_online:
            return
        logger.info("微信平台适配器已启动")
        await self.client.start_polling()


    async def terminate(self):
        """终止适配器"""
        self.client.shutdown_event.set()  # 设置关闭事件
        if self.ws:
            try:
                await self.ws.close()
            except Exception as e:
                logger.error(f"关闭WebSocket连接时出错: {e}")
        self.ws = None
        logger.info("微信平台适配器已终止")

    # 消息类型判断
    async def is_text_message(self, data: dict):
        """根据msg_type判断消息类型"""
        msg_type = data.get("msg_type", "")
        if msg_type == 49: # xml消息
            if self.redbag_enabled:
                # 开启红包助手
                if self.redbag_interval >= 0:
                    # 延迟领取红包
                    await asyncio.sleep(self.redbag_interval)
                await self.client.open_redbag(data)
                return None
        else:
            abm = await self.convert_message(data)
            return abm


    async def convert_message(self, data: dict):
        abm = AstrBotMessage()
        abm.message = []
        
        try:
            # 发送者
            from_user = data.get("from_user_name", {}).get("str", "")

            # 判断消息来源
            is_group = "@chatroom" in from_user

            # 判断消息类型
            abm.type = MessageType.GROUP_MESSAGE if is_group else MessageType.FRIEND_MESSAGE
            
            # 机器人的识别id
            abm.self_id = self.wxid

            # 会话id
            abm.message_id = data.get("msg_id", "")
            
            # 消息时间戳
            abm.timestamp = data.get("create_time", int(time.time()))
            
            if is_group:
                # 获取消息内容
                push_content = data.get("push_content", {})
                raw_content = data.get("content", {}).get("str", "")
                if "在群聊中@了你" in push_content:
                    # 情况1：处理 "长长久久在群聊中@了你"
                    nickname = push_content.split("在群聊中@了你")[0].strip()
                    actual_content = raw_content.split(":\n")[1]
                elif ":" in push_content:
                    # 情况2：处理 "用户昵称: 消息内容"
                    nickname, actual_content = push_content.split(":", 1)
                    nickname = nickname.strip()
                elif ":\n" in raw_content:
                    # 情况3：处理 "用户昵称:\n消息内容"
                    nickname, actual_content = raw_content.split(":\n", 1)
                    nickname = nickname.strip()

                # 去除nickname中的空格
                nickname = nickname.strip()
                abm.sender = MessageMember(user_id=raw_content.split(":\n")[0], nickname=nickname)
                abm.message.append(Plain(actual_content))
                abm.message_str = f"{nickname}: {actual_content}"
                abm.session_id = from_user
                abm.group_id = from_user
            else:
                if from_user == self.wxid:
                    return None
                    
                push_content = data.get("push_content", "")
                nickname = push_content.split(":")[0]
                # 去除nickname中的空格
                nickname = nickname.strip()
                content = data.get("content", {}).get("str", "")
                
                abm.sender = MessageMember(user_id=from_user, nickname=nickname)
                abm.message.append(Plain(content))
                abm.message_str = f"{nickname}: {content}"
                abm.session_id = from_user
            
            logger.debug(f"转换后的消息: {abm}\n")
            abm.raw_message = data
            return abm if abm.message else None

        except Exception as e:
            logger.error(f"消息转换失败: {e}\n原始数据: {data}")
            return None


    async def handle_msg(self, message: AstrBotMessage):
        """处理转换后的消息
    
        Args:
            message: 转换后的标准消息对象
        """
        from .wechat_event import WechatEvent
    
        # 从message.message数组中获取文本内容
        message_str = ""
        for component in message.message:
            if isinstance(component, Plain):
                message_str += component.text
            elif isinstance(component, Image):
                message_str += "[图片]"
            elif isinstance(component, Video):
                message_str += "[视频]"
            elif isinstance(component, Record):
                message_str += "[语音]"
            else: # 处理其他可能的组件类型
                 message_str += f"[{type(component).__name__}]"


        # 创建事件对象
        message_event = WechatEvent(
            message_str=message_str,        # 消息文本内容
            message_obj=message,            # 原始消息对象
            platform_meta=self.meta(),      # 平台元数据
            session_id=message.session_id,  # 会话ID
            client=self.client              # 微信客户端实例
        )
        
        # 提交事件到事件总线
        self.commit_event(message_event)
