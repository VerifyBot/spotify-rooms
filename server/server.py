import base64
import dataclasses
import sys
import contextlib
import hashlib
import logging
import sqlite3
import time
import traceback
import threading
import uuid

import coloredlogs
import spotipy
import yt_dlp.extractor.youtube

from utils import *

# setup simple logger
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
coloredlogs.install(level='INFO')


@dataclasses.dataclass()
class Room:
  id: int
  listeners_tokens: list[str] = dataclasses.field(default_factory=list)
  listeners: list[str] = dataclasses.field(default_factory=list)
  queue: list[dict] = dataclasses.field(default_factory=list)
  current_song: dict = dataclasses.field(default_factory=dict)
  song_base64: str = ''

  _start_time: float = None
  _duration = None

  @property
  def current_seek(self):
    if self._start_time is None:  # not playing
      return 0

    return time.time() - self._start_time

  def __str__(self):
    return f'Room {self.id} - {self.listeners=}, {self.queue=}, {self.current_song=}'

  def __repr__(self):
    return str(self)


class Routes:

  def ping_cmd(self, sock: socket.socket, msg: str = ''):
    """SOCKET COMMAND
    ping command"""
    return {"pong": msg}

  def register_client(self, sock: socket.socket, username: str, password: str):
    """SOCKET ROUTE -- RGST -- Register a user"""

    # check if the user exists
    resp = self.execute("""SELECT id FROM users WHERE username=?""", args=(username,),
                        fetchone=True)

    if resp is not None:
      return {"error": "Username is taken"}

    # hash the password (sha256)
    m = hashlib.sha256(password.encode() + b"spotishai" + username.encode())  # add a salt
    hashed_password = m.hexdigest()

    # insert the user
    self.execute("""INSERT INTO users (username, password) VALUES (?, ?);""",
                 args=(username, hashed_password))

    # create an auth token (random uuid)
    auth = str(uuid.uuid4())

    self.auths[auth] = username

    return {"auth": auth}

  def login_client(self, sock, username: str, password: str):
    """SOCKET ROUTE -- LOGN -- Login a user"""

    # check if credentials are correct

    # hash the password (sha256)
    m = hashlib.sha256(password.encode() + b"spotishai" + username.encode())  # add a salt
    hashed_password = m.hexdigest()

    resp = self.execute("""SELECT username FROM users WHERE username=? AND password=?;""",
                        args=(username, hashed_password), fetchone=True)

    if resp is None:
      return {"error": "Invalid username or password"}

    # check if the user is already logged in
    if username in self.auths.values():
      return {"auth": next(k for k, v in self.auths.items() if v == username), "username": resp[0]}

    # create an auth token (random uuid)
    auth = str(uuid.uuid4())
    self.auths[auth] = username

    return {"auth": auth, "username": resp[0]}

  def room_info(self, sock, auth, room: int):
    """SOCKET ROUTE -- ROOM -- Get the room info"""

    if auth not in self.auths:
      return {"error": "Invalid auth token"}

    if room < 1 or room > 6:
      return {"error": "Invalid room number"}

    room: Room = self.get_room(room)

    # logging.info(room)

    return {
      "listeners": [u for u in room.listeners if u != self.auths[auth]],
      "queue": room.queue,
      "current_song": room.current_song,
      "current_seek": room.current_seek,
    }

  def room_join(self, sock, auth, room: int):
    """SOCKET ROUTE -- RJOI -- Join a room"""

    if auth not in self.auths:
      return {"error": "Invalid auth token"}

    if room < 1 or room > 6:
      return {"error": "Invalid room number"}

    room: Room = self.get_room(room)
    username = self.auths[auth]

    # already in the room
    if auth in room.listeners_tokens:
      return {"status": "ok"}

    # in any other room?
    for r in self.rooms:
      if auth in r.listeners_tokens:
        r.listeners_tokens.remove(auth)
        r.listeners.remove(username)

    room.listeners_tokens.append(auth)
    room.listeners.append(username)

    return {"status": "ok"}

  def room_leave(self, sock, auth):
    """SOCKET ROUTE -- LEAV -- Leave the room"""

    if auth not in self.auths:
      return {"error": "Invalid auth token"}

    username = self.auths[auth]

    for room in self.rooms:
      if auth in room.listeners_tokens:
        room.listeners_tokens.remove(auth)
        room.listeners.remove(username)

    return {"status": "ok"}

  def search_songs(self, sock, auth, query: str):
    """SOCKET ROUTE -- SEAR -- Search for a song"""

    if auth not in self.auths:
      return {"error": "Invalid auth token"}

    self.spotify_api: spotipy.Spotify

    js = self.spotify_api.search(q=query, limit=5, type="track")

    logging.info('sending song results')

    songs = []

    for song in js['tracks']['items']:
      songs.append({
        "title": song['name'],
        "artist": song['artists'][0]['name'],
        "image_url": song['album']['images'][0]['url'],
        "id": song['id']
      })

    return {"songs": base64.b64encode(json.dumps(songs).encode()).decode()}

  def room_add_queue(self, sock, auth, song_id: str):
    """SOCKET ROUTE -- RQUE -- Add a song to the queue"""

    if auth not in self.auths:
      return {"error": "Invalid auth token"}

    # is in any room?
    room_id = None

    for room in self.rooms:
      if auth in room.listeners_tokens:
        room_id = room.id
        break

    if room_id is None:
      return {"error": "You are not in a room"}

    # get song
    song = self.spotify_api.track(song_id)

    if song is None:
      return {"error": "Invalid song id"}

    room: Room = self.get_room(room_id)

    # twice in a row?
    if (len(room.queue) > 0 and room.queue[-1]['id'] == song_id) or (
        len(room.queue) == 0 and room.current_song and room.current_song.get('id') == song_id):
      return {"error": "Song is already in the queue"}

    room.queue.append({
      "title": song['name'],
      "artist": song['artists'][0]['name'],
      "image_url": song['album']['images'][0]['url'],
      "id": song_id
    })

    logging.error(room)

    return {"status": "ok"}

  def room_skip(self, sock, auth):
    """SOCKET ROUTE -- RSKIP -- Skip the current song"""

    if auth not in self.auths:
      return {"error": "Invalid auth token"}

    # is in any room?
    room_id = None

    for room in self.rooms:
      if auth in room.listeners_tokens:
        room_id = room.id
        break

    if room_id is None:
      return {"error": "You are not in a room"}

    room: Room = self.get_room(room_id)

    # is the current song loading? starts with ⏳ -- no skip
    if room.current_song['title'].startswith('⏳'):
      return {"error": "Song is loading"}

    room.current_song = {}
    room.song_base64 = None

    return {"status": "ok"}

  def room_current(self, sock, auth):
    """SOCKET ROUTE -- RCUR -- Get the current song"""

    if auth not in self.auths:
      return {"error": "Invalid auth token"}

    # is in any room?
    room_id = None

    for room in self.rooms:
      if auth in room.listeners_tokens:
        room_id = room.id
        break

    if room_id is None:
      return {"error": "You are not in a room"}

    room: Room = self.get_room(room_id)

    return {
      "current_song": room.current_song,
      "current_seek": room.current_seek,
      "song_base64": room.song_base64
    }


def manage_songs(server):
  while True:
    for room in server.rooms:
      if room._duration and room.current_seek >= room._duration:
        # song is over
        room.current_song = {}
        room.song_base64 = None
        room._start_time = None

      if len(room.queue) == 0:
        continue

      # print(f'room: {room.id} - {room=}')

      if room.current_song == {}:
        curr = room.queue.pop(0)

        # get the song
        # no logs
        ydl_opts = {
          'outtmpl': 'downloads/%(id)s.%(ext)s',  # Output template for downloaded files

          # low quality audio, fastest download possible. mp3 format
          'format': 'bestaudio/best',
          'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
          }],

          'quiet': True,

        }

        # show loading
        room.current_song = {"title": f"⏳ {curr['title']}", "artist": f"{curr['artist']}",
                             "image_url": curr['image_url']}

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
          info = ydl.extract_info(f"ytsearch:{curr['title']} {curr['artist']}", download=False)
          video = info['entries'][0]
          video_id = video['id']

          file_ext = 'mp3'

          import os
          if not os.path.exists(f"downloads/{video_id}.{file_ext}"):
            # print('DOWNLOADING')
            ydl.download([video_id])
          else:
            pass
            # print('CACHED')

          room._duration = video['duration']
          # print(f'{room._duration=}')

          with open(f"downloads/{video_id}.{file_ext}", 'rb') as f:
            room.song_base64 = base64.b64encode(f.read()).decode()

        room.current_song = curr
        room._start_time = time.time()

        # with open('../client/sou.mp3', 'rb') as f:
        #   room.song_base64 = base64.b64encode(f.read()).decode()  # todo download with ytdl

    time.sleep(0.1)


class Server(Routes):
  """
  The server class handles and all the clients connected to it.
  """

  def __init__(self):
    self.server_sock = None
    self.client_socks = []

    self.kill_threads = False

    self.SERVER_ROUTES = {
      "PING": self.ping_cmd,
      "RGST": self.register_client,
      "LOGN": self.login_client,
      "ROOM": self.room_info,
      "JOIN": self.room_join,
      "LEAV": self.room_leave,
      "SONG": self.search_songs,
      "RQUE": self.room_add_queue,
      "RSKP": self.room_skip,
      "RCUR": self.room_current,
    }

    self.conn = sqlite3.connect('database.db', isolation_level=None, check_same_thread=False)
    self.auths = {}
    self.rooms = [Room(id=i) for i in range(1, 6 + 1)]

    auth_manager = spotipy.SpotifyOAuth(**config['spotify'])
    self.spotify_api = spotipy.Spotify(auth_manager=auth_manager)

    self.manage_songs_thread = threading.Thread(target=manage_songs, args=(self,), daemon=True)
    self.manage_songs_thread.start()

    self.sock_auth = {}  # mapping

  def get_room(self, room_id: int) -> Room:
    return self.rooms[room_id - 1]

  def execute(self, command, args=None, fetchall=False, fetchone=False, getid=False):
    """Executes a command."""
    cur = self.conn.cursor()

    cur.execute(command, args)

    resp = cur.fetchall() if fetchall else cur.fetchone() if fetchone else None

    if getid:
      resp = cur.lastrowid

    cur.close()

    return resp

  def run(self, ip=None, port=None):
    """
    The main function of the server.
    It handles new clients and creates a thread for each one.
    """

    # create socket and bind to port
    self.server_sock = socket.socket()
    self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # allow reuse of address:
    # If killing the server then starting it again works without
    # waiting for the port to be released

    try:
      self.server_sock.bind((ip or SERVER_IP, port or SERVER_PORT))  # bind to port and ip from config file
    except OSError:
      print('Port is perhaps unavailable')
      # print traceback
      logging.error(traceback.format_exc())
      return

    self.server_sock.listen(20)  # it basically means that at the same bit of a second we can read 20 clients.
    # it's not really a big deal for a small application like this.
    # NOTE: this is not the maximum number of clients that can connect to the server.

    threads = []
    client_id = 1

    while True:
      logging.info('[Main] Waiting for clients...')

      cli_sock, addr = self.server_sock.accept()  # ready accept a new client (BLOCKING)

      # create a thread for the new client
      t = threading.Thread(target=self.handle_client, args=(cli_sock, str(client_id), addr), daemon=True)
      t.start()
      threads.append(t)
      self.client_socks.append(cli_sock)

      client_id += 1

      # We can decide to kill the server by a certain condition.
      # This does not wait for the clients to disconnect (normally),
      # it just forces them to after they are done with the current job.
      if client_id > 100000000:  # for tests change it to 4
        logging.warning('[Main] Going down for maintenance')
        break

    socket.shutdown(socket.SHUT_WR)  # shutdown the socket (no reads or writes)
    self.kill_threads = True  # tell the threads to break their loops to close the socket

    # logging.info('[Main] waiting to all clients to die')

    # for t in threads:
    #   t.join()

    self.server_sock.close()  # close the server socket
    print('Bye ..')

  def logtcp(self, dir, tid, byte_data):
    """log direction, tid and all TCP byte array data"""

    s = byte_data.decode() if isinstance(byte_data, bytes) else byte_data
    if 'ROOM' in s:
      return

    if dir == 'sent':
      logging.info(f'{tid} S LOG:Sent\t>>>\t{byte_data}')
    else:
      logging.info(f'{tid} S LOG:Recieved\t<<<\t{byte_data}')

  def send_error(self, sock, tid, err, errmsg=None):
    """
    Send error message to client
    """
    msg = f's{MessageType.ERROR}EROR' + json.dumps({
      "error_code": err.eid if hasattr(err, "eid") else GeneralError.eid,
      "error": errmsg or str(err)
    })

    length_header = length_header_send(msg)

    with contextlib.suppress(ConnectionError):
      sock.sendall(length_header + msg.encode())
      self.logtcp('sent', tid, msg)

  def handle_client_message(self, sock, mdata, tid):
    """
    Do something with the client message. The message is already parsed.
    This function understands the message, does something with it and sends a response.
    """

    route = mdata['route']

    # unknown command
    if route not in self.SERVER_ROUTES: raise BadMessageError(f'Unknown route {route}')

    # possible items in mdata:
    mtype = mdata['type']
    mdata = mdata['data']  # data (raw/json)

    def try_func(**kw):
      # call function with arguments
      try:
        fun = self.SERVER_ROUTES[route]
        self.sock_auth[sock] = kw.get('auth')
        return fun(sock=sock, **kw)
      except ResponseGenerateError as e:
        return send_error(sock, tid, e.eid)

    if mtype == MessageType.ERROR:
      # client sent an error
      logging.info(f'Client sent error: {mdata=}')
      return route

    elif mtype == MessageType.RAW:
      resp = try_func(data=mdata)

    elif mtype == MessageType.JSON:
      if isinstance(mdata, list):
        resp = try_func(data=mdata)
      else:  # dict
        resp = try_func(**mdata)

    else:  # unstructured data, not supported.
      raise BadMessageError(f'Invalid message type')

    if route != 'ROOM':
      pass
      # print('#' * 20)
      # print(f'server is sending the client a message back:')
      # print(resp)
      # print('#' * 20)

    # get type
    if isinstance(resp, (dict, list)):
      otype = MessageType.JSON
      resp = json.dumps(resp).encode()
    else:
      otype = MessageType.RAW

    resp = f's{otype}{route}'.encode() + resp

    # get data length header
    length_header = length_header_send(resp)

    # send
    sock.sendall(length_header + resp)
    # logging.info(f"$ {length_header + resp}")
    if route != 'ROOM':
      self.logtcp('sent', tid, f'{route} {len(resp)} bytes')

    return route

  def handle_client(self, sock: socket.socket, tid, addr):
    """
    A thread dedicated for a single client.
    It is responsible for handling the client's requests.

    :param sock: The client's socket
    :param tid: The thread/client id
    :param addr: The client's address
    """
    logging.info(f'[+] Client {tid} connected from {addr}')

    while True:  # loop until client disconnects or sends EXIT command, or when a critical error occurs

      if self.kill_threads:  # if the server is shutting down, kill this thread
        logging.warning(f'Killing {tid}')
        break

      try:
        # wait for the client to send a message (BLOCKING)
        # this function follows the protocol rules and does not
        # give up until all the message is received.
        msg = fetch_all(sock)

        self.logtcp('recieved', tid, msg)

        mdata = parse_message_by_protocol(msg)  # parse the message by the protocol rules into a dict

        # handle the message, meaning that depending on the route, the server will do something
        # and send a response to the client. An error might occur, and it will be handled here (catch).
        cmd = self.handle_client_message(sock, mdata, tid)

        # client wants to go, bye bye
        if cmd == 'EXIT': break

      except OkCheck:  # client sent OK to validate the connection, send OK back.
        sock.send('OK'.encode())
        continue
      except DisconnectedError as e:
        logging.error(f'Client {tid} disconnected during recv()')
        with contextlib.suppress(Exception):  # already disconnected, so prolly wouldn't work
          self.send_error(sock, tid, e, 'Disconnected')
        break
      except BadMessageError as e:
        logging.error(f'Client {tid} sent bad message ({e})')
        self.send_error(sock, tid, e)
      except socket.error as err:
        logging.error(f'Socket Error exit client loop: err:  {err}')
        with contextlib.suppress(Exception):
          self.send_error(sock, tid, err, 'Socket Error')
        break
      except Exception as err:
        logging.error(f'General Error %s exit client loop: {err}')
        logging.error(traceback.format_exc())
        with contextlib.suppress(Exception):
          self.send_error(sock, tid, err, 'General Error')
        break

    logging.info(f'Client {tid} Exit')

    # remove from rooms
    auth = self.sock_auth.get(sock)

    if auth:
      for room in self.rooms:
        if auth in room.listeners_tokens:
          room.listeners_tokens.remove(auth)
          room.listeners.remove(self.auths[auth])

      with contextlib.suppress(KeyError):
        del self.sock_auth[sock]

    sock.close()


if __name__ == '__main__':
  if len(sys.argv) == 1:
    Server().run()
  else:
    try:
      Server().run(sys.argv[1], int(sys.argv[2]))
    except IndexError:
      print('Usage: server.py <server_ip> <server_port>')
