#! /usr/bin/env python3
# -*- coding:utf-8 -*-
"""
jetKVM RPC client that is able to send keyboard input to jetkvm.
Idea is borrowed from https://github.com/davehorner/jetkvm_control.

MIT License

Copyright (c) 2025 David Horner

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

from aiohttp import ClientError
from aiohttp import ClientResponse
from aiohttp import ClientSession
from aiohttp import ClientWebSocketResponse
from aiohttp import WSMessage
from aiohttp import WSMsgType
from aiortc import RTCConfiguration
from aiortc import RTCDataChannel
from aiortc import RTCIceCandidate
from aiortc import RTCIceServer
from aiortc import RTCPeerConnection
from aiortc import RTCSessionDescription
from aiortc.sdp import candidate_from_sdp as from_sdp
from base64 import b64decode
from base64 import b64encode
from dataclasses import dataclass
from functools import reduce
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Self
from typing import Set
from typing import Tuple
from typing import Type
from types import TracebackType
import asyncio
import json
import logging

# Set of WebSocket message types indicating a closed or errored connection.
WS_CLOSED = {WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED, WSMsgType.ERROR}


@dataclass
class KeyCombo:
    """
    Represents a combination of keys and modifiers to be sent.

    Attributes:
        modifiers (Set[int]):
            A set of modifier key codes (Shift, Ctrl, Alt, etc.).
        keys (Set[int]):
            A set of key codes to press.
        hold_keys (bool, optional):
            If True, the keys are held down until explicitly released. Defaults
            to False.
        hold_modifiers (bool, optional):
            If True, the modifiers are held down until explicitly released.
            Defaults to False.
        hold (Optional[int], optional):
            Duration in milliseconds to hold the keys down. Defaults to None.
        wait (Optional[int], optional):
            Duration in milliseconds to wait after sending the combo. Defaults
            to None.
        instant_release (Optional[bool], optional):
            If True, the keys are released immediately after being considered
            'pressed'. Defaults to None.
        clear_keys (Optional[bool], optional):
            If True, all currently held keys are released before processing
            this combo. Defaults to None.
    """

    modifiers: Set[int]
    keys: Set[int]
    hold_keys: bool = False
    hold_modifiers: bool = False
    hold: Optional[int] = None
    wait: Optional[int] = None
    instant_release: Optional[bool] = None
    clear_keys: Optional[bool] = None


class JetKey:
    """
    A client for interacting with a JetKVM instance over WebSockets and WebRTC.
    Provides functionality for logging in, sending keyboard input, and handling
    the WebRTC signaling process.
    """

    login_endpoint = "auth/login-local"
    logout_endpoint = "auth/logout"
    signalling_endpoint = "webrtc/signaling/client"
    ice_urls: List[str] = ["stun:stun.l.google.com:19302"]

    def __init__(self, host: str, port: int, password: str, logging_: bool = True):
        """
        Initializes JetKey with the host, port, and password.

        Args:
            host (str):The hostname or IP address of the JetKVM server.
            port (int):The port number of the JetKVM server.
            password (str):The password for authentication.
        """
        self.host: str = host
        self.port: int = port
        self.password: str = password
        log_level: int = logging.DEBUG if logging_ else logging.CRITICAL
        logging.basicConfig(level=log_level)
        self.logger = logging.getLogger(__name__)
        self.base_url: str = f"http://{self.host}:{self.port}/"
        self.login_url: str = self.base_url + self.login_endpoint
        self.logout_url: str = self.base_url + self.logout_endpoint
        self.signal_url: str = self.base_url + self.signalling_endpoint
        self.stop_event: asyncio.Event = asyncio.Event()

    async def __aenter__(self) -> Self:
        """
        Asynchronous context manager entry point. Initializes the client,
        logs in, establishes the WebRTC connection, and waits for the
        data channel to open.

        Returns:
            Self:The JetKey instance.
        """
        self.request_id: int = 0
        self.session: ClientSession = ClientSession()
        ice_servers: List[RTCIceServer] = [
            RTCIceServer(urls=ice_url) for ice_url in self.ice_urls
        ]
        config: RTCConfiguration = RTCConfiguration(iceServers=ice_servers)
        self.connection: RTCPeerConnection = RTCPeerConnection(configuration=config)
        self.data_channel: RTCDataChannel = self.connection.createDataChannel("rpc")

        @self.connection.on("signalingstatechange")
        def on_signalling_change() -> None:
            """
            Callback function called when the WebRTC signaling state changes.
            Logs the new signaling state.
            """
            state: str = self.connection.signalingState
            self.logger.debug(f"signal state changed to:{state}")

        @self.connection.on("icegatheringstatechange")
        def on_ice_candidate_change() -> None:
            """
            Callback function called when the ICE gathering state changes.
            Logs the new ICE gathering state.
            """
            state: str = self.connection.iceGatheringState
            self.logger.debug(f"ice gathering status changed to:{state}")

        @self.connection.on("connectionstatechange")
        def on_connection_state_change() -> None:
            """
            Callback function called when the WebRTC connection state changes.
            Logs the new connection state.
            """
            state: str = self.connection.connectionState
            self.logger.debug(f"Connection state is now:{state}")

        @self.data_channel.on("open")
        def on_open() -> None:
            """
            Callback function called when the WebRTC data channel is opened.
            Logs that the data channel has opened.
            """
            self.logger.debug("Data channel opened")

        @self.data_channel.on("message")
        def on_message(message: str) -> None:
            """
            Callback function called when a message is received on the data
            channel and logs the received message.

            Args:
                message:The received message.
            """
            self.logger.debug(f"message:{message}")

        @self.connection.on("icecandidate")
        async def on_ice_candidate(candidate) -> None:
            """
            Callback function called when a new ICE candidate is gathered.
            Sends the ICE candidate to the remote peer via the signaling server.

            This could probably be implemented like this:
            async def _send_candidate(self, candidate:RTCIceCandidate) -> None:
                '''
                Sends a local ICE candidate to the remote peer via the signaling
                server.

                Args:
                    candidate (RTCIceCandidate):The local ICE candidate to send.
                '''
                payload:str = json.dumps({
                    "type":"new-ice-candidate",
                    "data":{
                        "candidate":candidate.to_sdp(),
                        "sdpMid":candidate.sdpMid,
                        "sdpMLineIndex":candidate.sdpMLineIndex,
                    }
                })
                await self.socket.send_str(payload)

            Args:
                candidate (RTCIceCandidate):The gathered ICE candidate.
            """
            raise NotImplementedError

        self.stop_event.clear()
        asyncio.create_task(self._run())
        await self._wait_untill_open()
        return self

    async def __aexit__(
        self,
        exctype: Optional[Type[BaseException]],
        excinst: Optional[BaseException],
        exctb: Optional[TracebackType],
    ) -> None:
        """
        Asynchronous context manager exit point. Logs out, closes the
        WebRTC connection, data channel, and the aiohttp session.
        Cancels any pending asyncio tasks.

        Args:
            *err:Exception details if an exception occurred within the context.
        """
        if self.session:
            await self._logout(self.session)
            await self.session.close()
            del self.session

        if self.data_channel:
            self.data_channel.close()
            del self.data_channel

        if self.connection.sctp:
            await self.connection.sctp.stop()

        if self.connection:
            await self.connection.close()
            del self.connection

        self.stop_event.set()
        task: asyncio.Task
        for task in asyncio.all_tasks():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _login(self, session: ClientSession) -> bool:
        """
        Logs in to the JetKVM server using the provided password.

        Args:
            session (ClientSession):The aiohttp client session to use.

        Returns:
            bool:True if login was successful, raises an exception otherwise.

        Raises:
            Exception:If the login request fails.
        """
        payload: Dict[str, str] = {"password": self.password}
        response: ClientResponse
        async with session.post(url=self.login_url, json=payload) as response:
            if response.status == 200:
                return True
        raise Exception("Could not login!")

    async def _logout(self, session: ClientSession) -> bool:
        """
        Logs out from the JetKVM server.

        Args:
            session (ClientSession):The aiohttp client session to use.

        Returns:
            bool:True if logout was successful, raises an exception otherwise.

        Raises:
            Exception:If the logout request fails.
        """
        response: ClientResponse
        async with session.post(url=self.logout_url) as response:
            if response.status == 200:
                return True
        raise Exception("Could not logout!")

    async def _send_offer(self) -> None:
        """
        Creates and sends the WebRTC offer to the signaling server.

        Args:
            socket (ClientWebSocketResponse):The WebSocket connection to the
            signaling server.
        """
        offer: RTCSessionDescription = await self.connection.createOffer()
        await self.connection.setLocalDescription(offer)
        while self.connection.iceGatheringState != "complete":
            await asyncio.sleep(0.1)
        local_description_json: Dict[str, str] = {
            "sdp": self.connection.localDescription.sdp,
            "type": self.connection.localDescription.type,
        }
        payload_bytes: bytes = json.dumps(local_description_json).encode("utf-8")
        encoded_payload_str: str = b64encode(payload_bytes).decode("utf-8")
        offer_payload: str = json.dumps(
            {"type": "offer", "data": {"Sd": encoded_payload_str}}
        )
        await self.socket.send_str(offer_payload)

    async def _process_answer(self, message_data: bytes) -> None:
        """
        Processes the WebRTC answer received from the signaling server.
        Sets the remote description of the RTCPeerConnection.

        Args:
            message_data (dict):The 'data' part of the WebSocket message
            containing the SDP answer.
        """
        decoded_data: bytes = b64decode(message_data)
        encoded_json: Dict[str, str] = json.loads(decoded_data)
        session_answer: RTCSessionDescription = RTCSessionDescription(
            encoded_json["sdp"], encoded_json["type"]
        )
        await self.connection.setRemoteDescription(session_answer)

    async def _add_ice_candidate(self, message_data: Dict[str, str]) -> None:
        """
        Adds a received ICE candidate to the RTCPeerConnection.

        Args:
            message_data (dict):The 'data' part of the WebSocket message
            containing the ICE candidate details.
        """
        candidate: RTCIceCandidate = from_sdp(message_data["candidate"])
        spdmid: str
        spdmid = message_data["sdpMid"] if message_data["sdpMid"] != "" else "0"
        candidate.sdpMid = spdmid
        candidate.sdpMLineIndex = int(message_data["sdpMLineIndex"])
        await self.connection.addIceCandidate(candidate)

    async def _on_message(self, message_type: str, message_data: Any) -> None:
        """
        Handles incoming messages from the WebSocket signaling server.

        Args:
            message_type (str):The 'type' of the received message.
            message_data (dict):The 'data' part of the received message.
            socket (ClientWebSocketResponse):The WebSocket connection.
        """
        if message_type == "device-metadata":
            await self._send_offer()
        elif message_type == "answer":
            await self._process_answer(message_data)
        elif message_type == "new-ice-candidate":
            await self._add_ice_candidate(message_data)

    async def _run(self) -> None:
        """
        The main loop that maintains the WebSocket connection to the
        signaling server and processes incoming messages. Handles login,
        reconnections, and message dispatching.
        """
        session: ClientSession
        async with self.session as session:
            await self._login(session)
            while not self.stop_event.is_set():
                try:
                    self.socket: ClientWebSocketResponse
                    async with session.ws_connect(self.signal_url) as self.socket:
                        try:
                            ws_message: WSMessage
                            async for ws_message in self.socket:
                                if ws_message.type in WS_CLOSED:
                                    break
                                elif ws_message.type == WSMsgType.TEXT:
                                    ws_msg_data: str = ws_message.data
                                    msg: dict[str, Any] = json.loads(ws_msg_data)
                                    data: Optional[Any] = msg.get("data", None)
                                    type: Optional[str] = msg.get("type", None)
                                    if type and data:
                                        await self._on_message(type, data)
                        except json.JSONDecodeError as error:
                            self.logger.debug(f"Error in JSON message:{error}")
                            continue
                        except asyncio.CancelledError as error:
                            self.logger.debug(f"Async Task cancelled:{error}")
                            break
                        except Exception as error:
                            self.logger.debug(f"Websocket comm error:{error}")
                            # Wait before attempting reconnection
                            await asyncio.sleep(5)
                except ClientError as error:
                    self.logger.debug(f"WebSocket conn error:{error}")
                    # Wait longer if initial connection fails
                    await asyncio.sleep(10)
                except Exception as error:
                    self.logger.debug(f"Unexpected error in _run:{error}")
                    break
        self.logger.debug("WebSocket loop finished.")

    async def _wait_untill_open(self) -> None:
        """
        Waits asynchronously until the WebRTC data channel is in the 'open'
        state.
        """
        data_channel: RTCDataChannel = self.data_channel
        while not data_channel or not data_channel.readyState == "open":
            await asyncio.sleep(0.1)

    async def _send_rpc(self, method: str, params: dict) -> Optional[int]:
        """
        Sends an RPC (Remote Procedure Call) over the WebRTC data channel.


        Args:
            method (str):The name of the RPC method to call.
            params (dict):A dictionary of parameters to pass to the RPC method.

        Returns:
            Optional[int]:The request ID of the sent RPC, or None if the
                           data channel is not open.

        Raises:
            Exception:If sending the RPC fails.

        """
        if self.data_channel.readyState == "open":
            self.request_id += 1

            payload_str: str = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": method,
                    "params": params,
                    "id": self.request_id,
                }
            )

            try:
                self.data_channel.send(payload_str)
            except Exception as error:
                raise Exception(f"Failed to send RPC:{error}")
            return self.request_id
        return None

    ## keyboard specific functions

    def _char_to_hid(self, char: str) -> Optional[Tuple[int, int]]:
        """
        Converts a character to its HID (Human Interface Device) keycode.

        Args:
            char (str):The character to convert.

        Returns:
            Optional[Tuple[int, int]]:
                A tuple containing the modifier and keycode, or None if the
                character is not supported.
        """
        if not isinstance(char, str) or len(char) != 1:
            return None
        if "a" <= char <= "z":
            return (0, ord(char) - ord("a") + 0x04)
        elif "A" <= char <= "Z":
            return (0x02, ord(char.lower()) - ord("a") + 0x04)
        elif "1" <= char <= "9":
            return (0, ord(char) - ord("1") + 0x1E)
        elif char == "0":
            return (0, 0x27)
        elif char == " ":
            return (0, 0x2C)
        else:
            MAP: List[Tuple[str, Tuple[int, int]]] = [
                ("!", (0x02, 0x1E)),  # Shift + '1'
                ("@", (0x02, 0x1F)),  # Shift + '2'
                ("#", (0x02, 0x20)),  # Shift + '3'
                ("$", (0x02, 0x21)),  # Shift + '4'
                ("%", (0x02, 0x22)),  # Shift + '5'
                ("^", (0x02, 0x23)),  # Shift + '6'
                ("&", (0x02, 0x24)),  # Shift + '7'
                ("*", (0x02, 0x25)),  # Shift + '8'
                ("(", (0x02, 0x26)),  # Shift + '9'
                (")", (0x02, 0x27)),  # Shift + '0'
                ("-", (0, 0x2D)),
                ("_", (0x02, 0x2D)),
                ("=", (0, 0x2E)),
                ("+", (0x02, 0x2E)),
                ("[", (0, 0x2F)),
                ("{", (0x02, 0x2F)),
                ("]", (0, 0x30)),
                ("}", (0x02, 0x30)),
                ("\\", (0, 0x31)),
                ("|", (0x02, 0x31)),
                (";", (0, 0x33)),
                (":", (0x02, 0x33)),
                ("'", (0, 0x34)),
                ('"', (0x02, 0x34)),
                ("`", (0, 0x35)),
                ("~", (0x02, 0x35)),
                (",", (0, 0x36)),
                ("<", (0x02, 0x36)),
                (".", (0, 0x37)),
                (">", (0x02, 0x37)),
                ("/", (0, 0x38)),
                ("?", (0x02, 0x38)),
            ]
            for ch, pair in MAP:
                if ch == char:
                    return pair
            return None

    async def rpc_sendtext(self, text: str) -> None:
        """
        Sends text to the JetKVM by sending individual character keycodes.

        Args:
            text (str):The text to send.
        """
        for c in text:
            hid_data: Optional[Tuple[int, int]] = self._char_to_hid(c)
            modifier: int
            keycode: int
            params: dict[str, int | List[int]]
            if hid_data:
                modifier, keycode = hid_data
                params = {"modifier": modifier, "keys": [keycode]}
                await self._send_rpc("keyboardReport", params)
                await asyncio.sleep(0.01)
                params = {"modifier": 0, "keys": []}
                await self._send_rpc("keyboardReport", params)
                await asyncio.sleep(0.01)
            else:
                self.logger.debug(f"Unsupported character:{c}")

    async def _clear_keys(
        self, active_keys: Set[int], active_modifiers: int, combo: KeyCombo
    ) -> Tuple[Set[int], int]:
        """
        Clears (releases) all currently pressed keys and modifiers if the
        `clear_keys` flag is set in the KeyCombo.

        Args:
            active_keys (Set[int]):The set of currently active (pressed) keys.
            active_modifiers (int):The currently active modifier keys.
            combo (KeyCombo):The current key combination being processed.

        Returns:
            Tuple[Set[int], int]:The updated set of active keys and active
            modifiers.
        """
        if combo.clear_keys:
            dbg_msg: str = (
                "clear_keys flag active.Forcing release of all keys and modifiers."
            )
            self.logger.debug(dbg_msg)
            active_keys.clear()
            active_modifiers = 0
            await self._send_rpc("keyboardReport", {"modifier": 0, "keys": []})
            if combo.wait is not None:
                dbg_msg = f"Waiting {combo.wait}ms after clear_keys combo..."
                self.logger.debug(dbg_msg)
                await asyncio.sleep(combo.wait / 1000)
        return active_keys, active_modifiers

    async def _hold_keys(
        self,
        active_keys: Set[int],
        active_modifiers: int,
        hold_modifiers: set[int],
        combo: KeyCombo,
    ) -> Tuple[Set[int], int]:
        """
        Holds down the specified keys and modifiers for the duration specified
        in the KeyCombo.

        Args:
            active_keys (Set[int]):The set of currently active (pressed) keys.
            active_modifiers (int):The currently active modifier keys.
            hold_modifiers (set[int]): Set of modifiers that should be held.
            combo (KeyCombo):The current key combination being processed.

        Returns:
            Tuple[Set[int], int]:The updated set of active keys and active
            modifiers.
        """
        if isinstance(combo.hold, int):
            dbg_msg: str = (
                f"Holding keys for {combo.hold}ms - "
                f"Modifier:{active_modifiers:#04x}, "
                f"Keys:{sorted(list(active_keys))}"
            )
            self.logger.debug(dbg_msg)
            hold_duration_seconds: float = combo.hold / 1000
            await asyncio.sleep(hold_duration_seconds)
            if not combo.hold_keys:
                active_keys -= set(combo.keys)
            for modifier in combo.modifiers:
                if modifier not in hold_modifiers:
                    active_modifiers &= ~modifier
            dbg_msg = (
                "Releasing keys after hold duration. "
                f"New Modifier:{active_modifiers:#04x}, "
                f"New Keys:{sorted(list(active_keys))}"
            )
            self.logger.debug(dbg_msg)
            keys: list[int] = sorted(list(active_keys))
            params: Dict[str, int | List[int]]
            params = {"modifier": active_modifiers, "keys": keys}
            await self._send_rpc("keyboardReport", params)
        return active_keys, active_modifiers

    async def _release_keys(
        self,
        active_keys: Set[int],
        active_modifiers: int,
        hold_modifiers: set[int],
        combo: KeyCombo,
    ) -> Tuple[Set[int], int]:
        """
        Releases the specified keys, either immediately or after a hold duration.

        Args:
            active_keys (Set[int]):The set of currently active (pressed) keys.
            active_modifiers (int):The currently active modifier keys.
            hold_modifiers (set[int]):Set of modifiers that should be held.
            combo (KeyCombo):The current key combination being processed.

        Returns:
            Tuple[Set[int], int]:The updated set of active keys and active modifiers.
        """
        if combo.instant_release:
            dbg_msg: str = (
                "Instant Release:Before releasing, "
                f"active_keys:{active_keys}, "
                f"active_modifiers:{active_modifiers:#04x}"
            )
            self.logger.debug(dbg_msg)
            active_keys -= set(combo.keys)
            check_two: bool
            for modifier in combo.modifiers:
                check_two = not active_keys and modifier not in hold_modifiers
                if combo.instant_release or check_two:
                    active_modifiers &= ~modifier
            dbg_msg = (
                "Instant Release:After releasing, "
                f"keys_to_release:{set(combo.keys)}, "
                f"active_keys:{active_keys}, "
                f"active_modifiers:{active_modifiers:#04x}"
            )
            self.logger.debug(dbg_msg)
            keys: list[int] = sorted(list(active_keys))
            params: Dict[str, int | List[int]]
            params = {"modifier": active_modifiers, "keys": keys}
            await self._send_rpc("keyboardReport", params)
        return active_keys, active_modifiers

    async def _wait_after_press(self, combo: KeyCombo) -> None:
        """
        Waits for a specified duration after sending a key combination.

        Args:
            combo (KeyCombo):The current key combination being processed.
        """
        if isinstance(combo.wait, int):
            dbg_msg: str = f"Waiting {combo.wait}ms before processing next key combo..."
            self.logger.debug(dbg_msg)
            wait_duration_seconds: float = combo.wait / 1000
            await asyncio.sleep(wait_duration_seconds)

    async def send_key_combinations(self, key_combos: List[KeyCombo]) -> None:
        """
        Sends a sequence of key combinations to the JetKVM server.

        Args:
            key_combos (List[KeyCombo]):A list of KeyCombo objects representing
            the key combinations to send.
        """
        active_modifiers: int = 0
        active_keys: Set[int] = set()
        hold_modifiers: set[int] = set()

        for combo in key_combos:
            dbg_msg: str = (
                f"Processing combo -> Modifier:{combo.modifiers}, "
                f"Keys:{combo.keys}, Hold Keys:{combo.hold_keys}, "
                f"Hold Modifiers:{combo.hold_modifiers}, Hold:{combo.hold}, "
                f"Wait:{combo.wait}, Instant Release:{combo.instant_release}"
            )
            self.logger.debug(dbg_msg)

            if combo.hold_modifiers:
                hold_modifiers.update(combo.modifiers)

            if combo.hold_keys:
                # used to be hold_keys...
                active_keys.update(combo.keys)

            active_modifiers = reduce(lambda acc, val: acc | val, combo.modifiers, 0)
            active_keys.update(combo.keys)

            active_keys, active_modifiers = await self._clear_keys(
                active_keys, active_modifiers, combo
            )
            if combo.clear_keys:
                continue
            dbg_msg = (
                "Sending Key Press - "
                f"Modifier:{active_modifiers:#04x}, "
                "Keys:{sorted(list(active_keys))}"
            )
            self.logger.debug(dbg_msg)
            keys: list[int] = sorted(list(active_keys))
            params: Dict[str, int | List[int]]
            params = {"modifier": active_modifiers, "keys": keys}
            await self._send_rpc("keyboardReport", params)

            active_keys, active_modifiers = await self._hold_keys(
                active_keys, active_modifiers, hold_modifiers, combo
            )
            active_keys, active_modifiers = await self._release_keys(
                active_keys, active_modifiers, hold_modifiers, combo
            )
            dbg_msg = (
                f"End of combo:active_keys:{active_keys}, "
                f"active_modifiers:{active_modifiers:#04x}"
            )
            self.logger.debug(dbg_msg)
            await self._wait_after_press(combo)

        dbg_msg = (
            f"Finished processing all key combos. Final Held "
            f"Keys:{active_keys}, Final Held Modifier:{active_modifiers:#04x}"
        )
        self.logger.debug(dbg_msg)


async def main():
    """
    Main function to run the JetKVM client and send key combinations.
    """
    host: str = "ip or hostname of jetkvm"
    port: int = 80
    password: str = "password"
    jetkey: JetKey
    async with JetKey(host, port, password) as jetkey:
        key_combinations_to_send: List[KeyCombo] = [
            # KeyCombo(modifier=[0x01, 0x02], keys=[0x29], instant_release=True),  # ctrl alt esc
            KeyCombo(modifiers={0x08}, keys={0x13}, hold=100, wait=500),  # open projection modes
            KeyCombo(modifiers={0x00}, keys={0x13}, hold=100, wait=500),  # switch one down
            KeyCombo(modifiers={0x00}, keys={0x28}, hold=100, wait=200),  # hit enter
            KeyCombo(modifiers={0x00}, keys={0x29}, hold=100, wait=200),  # hit escape
            KeyCombo(
                modifiers={0x00}, keys={0x00}, clear_keys=True, wait=100
            ),                                                            # Release all held modifiers
        ]
        await jetkey.send_key_combinations(key_combinations_to_send)
        await asyncio.sleep(0.1)


if __name__ == "__main__":
    asyncio.run(main())
