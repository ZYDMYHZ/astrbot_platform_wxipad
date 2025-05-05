import asyncio
import datetime
import json
import websockets
import xml.etree.ElementTree as ET
from urllib.parse import urlparse, parse_qs

import aiohttp

from astrbot.api import logger
from astrbot.api.message_components import Plain, Image, At, Record, Video
from astrbot.api.platform import AstrBotMessage, MessageMember, MessageType
from astrbot.core.platform.astr_message_event import AstrMessageEvent


class ipad855Client:
    """针对 ipad855 的实现。
    """

    def __init__(
        self,
        host: str,
        port: int,
        token: str,
        ws_url: str,
        event_queue: asyncio.Queue,
    ):
        if not host or not port:
            raise ValueError("host 和 port 不能为空")

        if isinstance(port, str):
            port = int(port)

        self.token = token
        self.headers = {"accept": "application/json"}

        self.host = host
        self.port = port
        self.base_url = f"http://{self.host}:{self.port}"
        self.ws_url = ws_url
        self.event_queue = event_queue
        
        # 登录相关属性
        self.login_status = False
        self.qr_code_data = None
        self.login_check_interval = 5  # 登录检查间隔（秒）
        self.login_timeout = 300  # 登录超时时间（秒）
        
        # 初始化 aiohttp 客户端
        self.session = aiohttp.ClientSession()

        # 用户信息缓存
        self.userrealnames = {}

        self.shutdown_event = asyncio.Event()

    async def keepalive(self, websocket):
        """维持WebSocket连接的心跳"""
        while not self.shutdown_event.is_set():
            await asyncio.sleep(15)
            try:
                await websocket.ping()
                logger.debug("发送WebSocket心跳包")
            except Exception as e:
                logger.warning(f"心跳发送失败: {e}")
                break

    async def start_polling(self):
        """启动WebSocket连接"""
        while not self.shutdown_event.is_set():
            full_ws_url = f"{self.ws_url}?key={self.token}"
            try:
                async with websockets.connect(
                    full_ws_url,
                    ping_interval=20,
                    ping_timeout=10
                ) as websocket:
                    logger.debug("WebSocket连接成功建立")
                    
                    # 启动心跳任务
                    keepalive_task = asyncio.create_task(self.keepalive(websocket))
                    
                    try:
                        while not self.shutdown_event.is_set():
                            try:
                                message = await asyncio.wait_for(websocket.recv(), timeout=30)
                                json_message = json.loads(message)
                                logger.debug(f"格式化后的消息: {json_message}\n")
                                await getattr(self, 'on_message_received')(json_message)
                            except asyncio.TimeoutError:
                                continue
                    finally:
                        keepalive_task.cancel()
                        
            except Exception as e:
                logger.error(f"WebSocket连接错误: {e}")
                if not self.shutdown_event.is_set():
                    await asyncio.sleep(5)


    async def check_and_login(self):
        """检查在线状态，如果不在线则获取登录二维码"""
        try:
            # 检查在线状态
            online = await self.check_online()
            if online:
                # logger.info("当前设备已在线")
                self.login_status = True
                return True
            
            # 如果不在线，获取登录二维码
            logger.warning("设备不在线，正在获取登录二维码...")
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/login/GetLoginQrCodeNewX",
                    params={"key": self.token},
                    json={
                        "Check": False,
                        "Proxy": ""
                    },
                    headers=self.headers,
                ) as resp:
                    if resp.status != 200:
                        logger.error(f"获取登录二维码失败，状态码: {resp.status}")
                        return False
                        
                    try:
                        json_blob = await resp.json()
                        if "Code" not in json_blob:
                            logger.error("响应中没有 Code 字段")
                            return False
                            
                        if json_blob["Code"] != 200:  # 修改为200，因为成功响应是200
                            error_text = json_blob.get("Text", "未知错误")
                            logger.error(f"获取登录二维码失败: {error_text}")
                            return False
                        
                        if "Data" not in json_blob or "QrCodeUrl" not in json_blob["Data"]:
                            logger.error("响应中没有 QrCodeUrl 字段")
                            return False
                            
                        self.qr_code_data = json_blob["Data"]["QrCodeUrl"]
                        logger.info(f"请使用微信扫描以下二维码登录: {self.qr_code_data}")
                        logger.info(f"提示: {json_blob['Data'].get('Txt', '')}")
                        
                        # 开始轮询检查登录状态
                        start_time = datetime.datetime.now()
                        while True:
                            if (datetime.datetime.now() - start_time).seconds > self.login_timeout:
                                logger.error("登录超时")
                                return False
                                
                            login_status = await self.check_login_status()
                            if login_status:
                                self.login_status = True
                                logger.info("ipad微信登录成功")
                                return True
                            
                            if self.shutdown_event.is_set():
                                logger.info("适配器关闭，退出登录")
                                return False
                                
                            await asyncio.sleep(self.login_check_interval)
                    except ValueError as e:
                        logger.error(f"解析响应 JSON 失败: {e}")
                        return False
                    
        except Exception as e:
            logger.error(f"检查在线状态或获取二维码时发生错误: {e}")
            return False


    async def check_login_status(self):
        """检查登录状态"""     
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/login/CheckLoginStatus",
                    params={"key": self.token},
                    headers=self.headers,
                ) as resp:
                    if resp.status != 200:
                        logger.error(f"检查登录状态失败，状态码: {resp.status}")
                        return False
                        
                    try:
                        json_blob = await resp.json()
                        if "Code" not in json_blob:
                            logger.error("响应中没有 Code 字段")
                            return False
                            
                        if json_blob["Code"] != 200:
                            # 如果扫码状态检查失败，再检查一下实际在线状态
                            online = await self.check_online()
                            if online:
                                # logger.info("设备已在线")
                                return True
                                
                            error_text = json_blob.get("Text", "未知错误")
                            # logger.error(f"检查登录状态失败: {error_text}")
                            return False
                            
                        if "Data" not in json_blob:
                            logger.error("响应中没有 Data 字段")
                            return False
                            
                        data = json_blob["Data"]
                        state = data.get("state", -1)
                        
                        # 状态说明：
                        # 0: 未扫描
                        # 1: 已扫描，等待确认
                        # 2: 已确认，登录成功
                        if state == 0:
                            logger.info(f"等待扫描二维码... 有效期剩余: {data.get('effective_time', 0)}秒")
                            return False
                        elif state == 1:
                            logger.info("二维码已扫描，请在手机上确认登录")
                            return False
                        elif state == 2:
                            logger.info("ipad微信登录成功")
                            return True
                        else:
                            logger.error(f"未知的登录状态: {state}")
                            return False
                            
                    except ValueError as e:
                        logger.error(f"解析响应 JSON 失败: {e}")
                        return False
        except Exception as e:
            logger.error(f"检查登录状态时发生错误: {e}")
            return False


    async def logout(self):
        """登出"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/login/Logout",
                    params={"key": self.token},
                    headers=self.headers,
                ) as resp:
                    json_blob = await resp.json()
                    if json_blob["Code"] != 0:
                        logger.error(f"登出失败: {json_blob}")
                        return False
                    
                    self.login_status = False
                    logger.info("登出成功")
                    return True
        except Exception as e:
            logger.error(f"登出时发生错误: {e}")
            return False


    async def check_online(self):
        """检查是否在线。"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/login/GetLoginStatus",
                    params={"key": self.token},
                    headers=self.headers,
                ) as resp:
                    if resp.status != 200:
                        logger.error(f"检查在线状态失败，状态码: {resp.status}")
                        return False
                        
                    try:
                        json_blob = await resp.json()
                        if "Code" not in json_blob:
                            logger.error("响应中没有 Code 字段")
                            return False
                            
                        if json_blob["Code"] != 200:
                            error_text = json_blob.get("Text", "未知错误")
                            logger.error(f"检查在线状态失败: {error_text}")
                            return False
                            
                        # 检查 Data 字段中的 loginState
                        if "Data" in json_blob and "loginState" in json_blob["Data"]:
                            login_state = json_blob["Data"]["loginState"]
                            if login_state == 1:  # 1 表示在线
                                logger.info(f"授权到期时间: {json_blob['Data'].get('expiryTime', '')}")
                                logger.info(f"{json_blob['Data'].get('loginErrMsg', '')}")
                                logger.info(f"{json_blob['Data'].get('onlineTime', '')}")
                                logger.info(f"{json_blob['Data'].get('totalOnline', '')}")
                                self.login_status = True
                                return True
                            else:
                                logger.error(f"账号不在线，状态码: {login_state}")
                                return False
                        else:
                            logger.error("响应中没有 loginState 字段")
                            return False
                            
                    except ValueError as e:
                        logger.error(f"解析响应 JSON 失败: {e}")
                        return False
        except Exception as e:
            logger.error(f"检查在线状态时发生错误: {e}")
            return False


    async def open_redbag(self, data: dict):
        """打开微信红包"""
        try:
            # Parse the XML content
            content = data['content']['str']
            xml_start = content.find('<msg>')
            xml_end = content.find('</msg>') + 6
            xml_content = content[xml_start:xml_end]
            
            root = ET.fromstring(xml_content)
            wcpayinfo = root.find('.//wcpayinfo')
            
            # Extract required fields
            native_url = wcpayinfo.find('nativeurl').text
            url = wcpayinfo.find('url').text
            
            # Parse URL parameters
            url_params = parse_qs(urlparse(url).query)
            native_params = parse_qs(urlparse(native_url).query)
            
            payload = {
                "Limit": 0,
                "NativeURL": native_url,
                "URLItem": {
                    "ChannelID": native_params.get('channelid', [''])[0],
                    "MsgType": native_params.get('msgtype', [''])[0],
                    "SendID": native_params.get('sendid', [''])[0],
                    "SendUserName": native_params.get('sendusername', [''])[0],
                    "ShowSourceMac": "",
                    "ShowWxPayTitle": native_params.get('showwxpaytitle', [''])[0],
                    "Sign": native_params.get('sign', [''])[0],
                    "Ver": native_params.get('ver', [''])[0]
                }
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/pay/OpenRedEnvelopes?key={self.token}",
                    headers=self.headers,
                    json=payload
                ) as resp:
                    json_blob = await resp.json()
                    logger.debug(f"拆红包结果: {json_blob}")
                    return json_blob
                    
        except Exception as e:
            logger.error(f"拆红包时发生错误: {e}")
            return None


    async def post_text(self, to_wxid: str, content: str, ats: str = ""):
        """发送文本消息
        
        Args:
            to_wxid: 接收者的微信ID
            content: 消息内容
            ats: @用户的微信ID列表，多个用逗号分隔
        """
        key = self.token
        payload = {
            "MsgItem": [
                {
                "AtWxIDList": [
                    *ats.split(",")
                ],
                "ImageContent": "",
                "MsgType": 0,
                "TextContent": content,
                "ToUserName": to_wxid,
                }
            ]
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/message/SendTextMessage?key={key}", headers=self.headers, json=payload
            ) as resp:
                json_blob = await resp.json()
                logger.debug(f"发送消息结果: {json_blob}")


    async def post_image(self, to_wxid, image_url: str):
        """发送图片消息
        
        Args:
            to_wxid: 接收者的微信ID
            image_url: 图片URL
        """
        key = self.token

        payload = {
            "MsgItem": [
                {
                "AtWxIDList": [
                    "string"
                ],
                "ImageContent": image_url,
                "MsgType": 0,
                "TextContent": "",
                "ToUserName": to_wxid,
                }
            ]
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/message/SendImageNewMessage?key={key}", headers=self.headers, json=payload
            ) as resp:
                json_blob = await resp.json()
                logger.debug(f"发送图片结果: {json_blob}")


    async def post_voice(self, to_wxid: str, voicedata: str, VoiceFormat: int = 1):
        """发送语音消息
        
        Args:
            to_wxid: 接收者的微信ID
            voicedata: 带MIME type前缀的Base64字符串
        """
        key = self.token

        payload = {
            "ToUserName": to_wxid,
            "VoiceData": voicedata,
            "VoiceFormat": 1,  # 语音格式，1表示AMR，2表示MP3
            "VoiceSecond,": 0  # 语音时长，单位为秒
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/message/SendVoice?key={key}", headers=self.headers, json=payload
            ) as resp:
                json_blob = await resp.json()
                logger.debug(f"发送语音结果: {json_blob}")


    async def post_video(self, to_wxid: str, VideoData: str):
        """发送视频消息

        Args:
            to_wxid: 接收者的微信ID
            VideoData: 视频数据
        """
        key = self.token

        payload = {
            "ThumbData": "",
            "ToUserName": to_wxid,
            "VideoData": [
                0
            ]
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/message/CdnUploadVideo?key={key}", headers=self.headers, json=payload
            ) as resp:
                json_blob = await resp.json()
                logger.debug(f"发送视频结果: {json_blob}")

    
    