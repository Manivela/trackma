# This file is part of Trackma.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

import json
import os
import re
import socket
import threading
import time
import urllib.parse

from trackma import utils
from trackma.tracker import tracker


class MPVOSXTracker(tracker.TrackerBase):
    name = 'Tracker (mpv-osx)'
    
    def __init__(self, messenger, tracker_list, config, watch_dirs, redirections=None):
        self.sock_path = os.path.join(os.path.expanduser("~"), ".config", "mpv", "socket")
        self.custom_sock_path = config.get('mpv_socket_path')
        if self.custom_sock_path:
            self.sock_path = os.path.expanduser(self.custom_sock_path)
            
        self.active = False
        self.sock = None
        self.last_state = utils.Tracker.NOVIDEO
        self.playing_status = True
        
        super().__init__(messenger, tracker_list, config, watch_dirs, redirections)
        
    def _connect_socket(self):
        try:
            self.msg.info("Connecting to socket at %s" % self.sock_path)
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.connect(self.sock_path)
            self.sock.settimeout(1)
            return True
        except (socket.error, FileNotFoundError) as e:
            self.msg.warn("Failed to connect to socket: %s" % e)
            self.sock = None
            return False
            
    def _send_command(self, command):
        if not self.sock:
            if not self._connect_socket():
                return None
                
        try:
            request_id = int(time.time() * 1000)
            cmd = {"command": command, "request_id": request_id}
            
            cmd_str = json.dumps(cmd) + "\n"
            self.sock.send(cmd_str.encode("utf-8"))
            
            response = b""
            try:
                while True:
                    chunk = self.sock.recv(4096)
                    if not chunk:
                        break
                    response += chunk
                    if b'\n' in chunk:
                        break
            except socket.timeout:
                pass
            
            if response:
                return json.loads(response.decode("utf-8"))
            return None
        except Exception as e:
            self.msg.warn(f"Socket error: {e}")
            self.sock = None
            return None
            
    def _observe_playback(self):
        while self.active:
            if not self.sock and not self._connect_socket():
                time.sleep(1)
                continue
                
            filename_response = self._send_command(["get_property", "filename"])
            path_response = self._send_command(["get_property", "path"])
            pause_response = self._send_command(["get_property", "pause"])
            media_title_response = self._send_command(["get_property", "media-title"])
            
            if not filename_response:
                if self.last_state != utils.Tracker.NOVIDEO:
                    self.update_show_if_needed(utils.Tracker.NOVIDEO, None)
                    self.last_state = utils.Tracker.NOVIDEO
                time.sleep(1)
                continue
                
            paused = False
            if pause_response and "data" in pause_response:
                paused = pause_response["data"]
            
            filename = None
            if media_title_response and "data" in media_title_response:
                filename = media_title_response["data"]
            elif path_response and "data" in path_response:
                filename = path_response["data"]
            elif filename_response and "data" in filename_response:
                filename = filename_response["data"]
                
            if filename:
                state = utils.Tracker.PLAYING
                if paused:
                    if not self.timer_paused:
                        self.pause_timer()
                else:
                    if self.timer_paused:
                        self.resume_timer()
                
                if state != self.last_state or filename != self.last_filename:
                    (state, show_tuple) = self._get_playing_show(filename)
                    if show_tuple:
                        self.update_show_if_needed(state, show_tuple)
                    else:
                        self.update_show_if_needed(state, None)
                    self.last_state = state
            else:
                if self.last_state != utils.Tracker.NOVIDEO:
                    self.update_show_if_needed(utils.Tracker.NOVIDEO, None)
                    self.last_state = utils.Tracker.NOVIDEO
                    
            time.sleep(1)
    
    def observe(self, config, watch_dirs):
        self.msg.info("Using macOS mpv socket tracker")
        self.active = True
        
        if not os.path.exists(self.sock_path):
            self.msg.warn("mpv socket not found at %s. Make sure to configure mpv with 'input-ipc-server' option." % self.sock_path)
            self.msg.warn("Add 'input-ipc-server=~/.config/mpv/socket' to your ~/.config/mpv/mpv.conf file.")
        
        thread = threading.Thread(target=self._observe_playback)
        thread.daemon = True
        thread.start()