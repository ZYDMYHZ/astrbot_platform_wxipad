import base64
from typing import Optional
import wave
import traceback

from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.platform import AstrBotMessage, PlatformMetadata
from astrbot.api.message_components import (
    Plain,
    Image,
    Record,
    At,
    File,
    Video,
    WechatEmoji as Emoji,
)
from astrbot.core.utils.io import save_temp_img, download_file
from astrbot.core.utils.tencent_record_helper import wav_to_tencent_silk
from astrbot import logger
import os
import uuid
from .client import ipad855Client


def get_wav_duration(file_path):
    with wave.open(file_path, "rb") as wav_file:
        file_size = os.path.getsize(file_path)
        n_channels, sampwidth, framerate, n_frames = wav_file.getparams()[:4]
        if n_frames == 2147483647:
            duration = (file_size - 44) / (n_channels * sampwidth * framerate)
        elif n_frames == 0:
            duration = (file_size - 44) / (n_channels * sampwidth * framerate)
        else:
            duration = n_frames / float(framerate)
        return duration

class WechatEvent(AstrMessageEvent):
    """"
    微信事件

    Args:
        message_str (str): 消息内容
        message_obj (AstrBotMessage): 消息对象
        platform_meta (PlatformMetadata): 平台元数据
        session_id (str): 会话ID
        client (Optional[object]): 客户端对象
    """
    def __init__(
        self,
        message_str: str,
        message_obj: AstrBotMessage,
        platform_meta: PlatformMetadata,  
        session_id: str,
        client: Optional[object] = None
    ):
        super().__init__(message_str, message_obj, platform_meta, session_id)
        self.client = client


    @staticmethod
    async def send_with_client(message: MessageChain, to_wxid: str, client: ipad855Client):
        if not to_wxid:
            logger.error("无法获取到 to_wxid。")
            return

        # 修改这部分代码
        ats = []
        ats_names = []
        for comp in message.chain:
            if isinstance(comp, At):
                ats.append(str(comp.qq))  # 确保转换为字符串
                ats_names.append(comp.name)

        for comp in message.chain:
            if isinstance(comp, Plain):
                text = comp.text
                payload = {
                    "to_wxid": to_wxid,
                    "content": text,
                }
                if ats:  # 直接使用ats列表，不再尝试split
                    ats_names_str = f"@{' @'.join(ats_names)}"
                    text = f"{ats_names_str} {text}"
                    payload["content"] = text
                    payload["ats"] = ",".join(ats)  # 在这里将列表转为逗号分隔字符串
                await client.post_text(to_wxid, text, payload.get("ats", ""))
            elif isinstance(comp, Image):
                img_path = await comp.convert_to_file_path()
                with open(img_path, "rb") as f:
                    img_base64 = base64.b64encode(f.read()).decode()
                await client.post_image(to_wxid, img_base64)
            elif isinstance(comp, Record):
                # 默认已经存在 data/temp 中
                record_path = await comp.convert_to_file_path()
                
                silk_path = f"data/temp/{uuid.uuid4()}.silk"
                try:
                    # 获取文件扩展名并转换为小写
                    ext = os.path.splitext(record_path)[1].lower()
                    
                    if ext == '.mp3':
                        # MP3处理逻辑
                        with open(record_path, 'rb') as f:
                            mp3_data = f.read()
                        base64_data = f"data:audio/mpeg;base64,{base64.b64encode(mp3_data).decode()}"
                        voiceformat = 2  # MP3格式
                        
                    elif ext == '.wav':
                        # WAV处理逻辑
                        with open(silk_path, 'rb') as f:
                            silk_data = f.read()
                        base64_data = f"data:audio/wav;base64,{base64.b64encode(silk_data).decode()}"
                        voiceformat = 1  # WAV/Silk格式
                    else:
                        raise ValueError(f"不支持的音频格式: {ext}")
                        
                    logger.info(f"语音文件格式转换完成: {record_path} -> {ext.upper()}")
                        
                    await client.post_voice(to_wxid, base64_data, voiceformat)
                    
                except Exception as e:
                    logger.error(f"语音处理失败: {traceback.format_exc()}")
                    await client.post_text(to_wxid, f"语音处理错误: {str(e)}")
                finally:
                    # 清理临时文件
                    for f in [record_path, silk_path]:
                        try:
                            if f and os.path.exists(f):
                                os.remove(f)
                        except:
                            pass
            elif isinstance(comp, Emoji):
                await client.post_emoji(to_wxid, comp.md5, comp.md5_len, comp.cdnurl)
            elif isinstance(comp, At):
                pass
            else:
                logger.debug(f"ipad 忽略: {comp.type}")
    

    async def send(self, message: MessageChain):
        to_wxid = self.message_obj.raw_message.get("to_wxid") or \
          self.message_obj.raw_message.get("from_user_name", {}).get("str")
        logger.debug(f"to_wxid: {self.message_obj}")
        await WechatEvent.send_with_client(message, to_wxid, self.client)
        await super().send(message)