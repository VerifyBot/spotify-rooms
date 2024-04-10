import base64
import io
import logging
import socket
import threading
import time
import traceback

import flet
import pygame.mixer
from flet import Text
import coloredlogs

coloredlogs.install(level='INFO')

from models import *
from components import *


class ExitResponded(Exception):
  """Raised when the server responds ok with the exit request (easy to stop the loop)"""
  pass


def exit_resp(sock: socket.socket):
  raise ExitResponded


socket_lock = threading.Lock()


def handle_server_response(sock: socket.socket, mdata: dict):
  """
   Handle messages that come from the server.
   Message is already parsed in the dict and ready to be handled.
  """

  route = mdata['route']
  mtype = mdata['type']

  # possible items in mdata:
  mtype = mdata['type']
  mdata = mdata['data']  # data (raw/json)

  if mtype == MessageType.ERROR:
    # This is where we can handle the specific error codes with specific messages
    # for now we just print the error code and message, but we can do more.
    print(f'Server responded with error: {mdata}')

    # check if the server is still connected
    # because some errors are critical (like the server shutting down) and we should exit
    # but some are fine like unknown command or bad arguments.
    if not is_alive(sock):  # We send an OK? message to the server to check if it's still alive
      raise ConnectionError
    return mdata

  elif mtype not in [MessageType.JSON, MessageType.RAW]:
    print(f'Server response (unstructured): {margs}')
    return

  # print the response
  # print(f'Server responded to {route}:\n\t{mdata=}')

  return mdata


def send_to_server(sock: socket.socket, msg_type: int, msg_route: str, msg_data):
  """
  Send a emssage to the server, following the protocol
  """

  with socket_lock:
    if msg_type in [MessageType.JSON, MessageType.ERROR]:
      msg_data = json.dumps(msg_data)
      msg = f"c{msg_type}{msg_route}{msg_data}".encode()
    else:
      msg = f"c{msg_type}{msg_route}".encode() + msg_data
    length_header = length_header_send(msg)

    try:
      sock.send(length_header + msg)

      if msg_route != 'ROOM':  # it's in loop so avoid spamming
        logging.info(f'[@] Sent message \n\t{msg_type=}\n\t{msg_route=}\n\t{msg_data[:100]=}')
        # logging.info(length_header + msg)

      # we expect a response from the server, so we wait for it
      resp = fetch_all(sock)
      mdata = parse_message_by_protocol(resp)

      return handle_server_response(sock, mdata)
    except ConnectionError:
      logging.warning('Connection to server was lost')
      return "EXIT_SIGNAL"
    except ExitResponded:
      logging.warning("Server tells you ok, bye bye")
      return "EXIT_SIGNAL"

    except DisconnectedError:
      logging.error(f'Server disconnected during recv()')
      return "EXIT_SIGNAL"

    except BadMessageError as e:
      logging.error(f'Server sent bad message ({e})')
      return "EXIT_SIGNAL"

    except socket.error as err:
      logging.error(f'Socket Error exit client loop: err:  {err}')
      return "EXIT_SIGNAL"

    except Exception as err:
      logging.error(f'General Error %s exit client loop: {err}')
      logging.error(traceback.format_exc())
      return "EXIT_SIGNAL"


class API:
  def __init__(self, client: socket.socket, page: flet.Page):
    self.client = client
    self.page = page

    self.token = None

  def _send(self, msg_type: int, msg_route: str, msg_data):
    resp = send_to_server(self.client, msg_type, msg_route, msg_data)

    if resp == "EXIT_SIGNAL":
      self.page.window_close()
      return dict(error="Server disconnected")

    return resp

  def login(self, username: str, password: str):
    """
    Login to your account
    """
    resp = self._send(MessageType.JSON, "LOGN", dict(
      username=username, password=password
    ))

    if err := resp.get('error'):
      return False, err

    self.token = resp['auth']
    return True, None

  def register(self, username: str, password: str):
    """
    Register a new account
    """
    resp = self._send(MessageType.JSON, "RGST", dict(
      username=username, password=password
    ))

    if err := resp.get('error'):
      return False, err

    self.token = resp['auth']
    return True, None

  def logout(self):
    self.token = None

  def get_room_info(self, room: int) -> RoomInfo:
    resp = self._send(MessageType.JSON, "ROOM", dict(auth=self.token, room=room))

    return RoomInfo(
      listeners=resp['listeners'],
      queue=[Song(**song) for song in resp['queue']],
      current_song=Song(**resp['current_song']),
      current_seek=resp['current_seek'],
    )

  def get_current_song(self) -> dict:
    resp = self._send(MessageType.JSON, "RCUR", dict(auth=self.token))
    return resp

  def join_room(self, room: int):
    resp = self._send(MessageType.JSON, "JOIN", dict(auth=self.token, room=room))
    return resp

  def leave_room(self):
    return self._send(MessageType.JSON, "LEAV", dict(auth=self.token))

  def search_songs(self, query: str) -> list[Song]:
    resp = self._send(MessageType.JSON, "SONG", dict(auth=self.token, query=query))

    return [Song(**song) for song in json.loads(base64.b64decode(resp['songs']).decode())]

  def send_add_to_queue(self, song: Song):
    return self._send(MessageType.JSON, "RQUE", dict(auth=self.token, song_id=song.id))

  def skip_song(self):
    return self._send(MessageType.JSON, "RSKP", dict(auth=self.token))


class Screens:
  """
  Manage the screens and the state of the app:
  - Register screen
  - Login screen
  - Home screen (list of rooms)
  - Room screen
  """

  def __init__(self, page: ft.Page, api: API):
    self.page = page
    self.api = api

    self.current_room = None
    self.queue_component: ft.Container = None
    self.listeners_component: ft.Container = None
    self.current_song_component: SongCard = None
    self.search_results_component: ft.Column = None
    self.search_query_ref: ft.Ref = None

    # audio manager
    pygame.mixer.init()

    self.currently_playing = None

    def do_logout(e: ControlEvent):
      self.api.logout()
      self.login()

    self.title = ft.Text(value='Spotify Rooms', text_align="start", size=40, weight="bold", color='green')
    self.logged_title = ft.Row([
      self.title, IconButton(icon=ft.icons.LOGOUT, tooltip="Log out", on_click=do_logout)],
      alignment="center")
    self.room_title = ft.Row([
      IconButton(icon=ft.icons.HOME, tooltip="Back to rooms", on_click=self.home),
      *self.logged_title.controls
    ], alignment="center")

  def show_dialog(self, message: str, on_close=None):
    """
    Display a dialog with a message
    :param message: The message to display
    """

    def close_dlg(e: ControlEvent):
      dlg.open = False
      self.page.update()

      if on_close:
        on_close()

    dlg = ft.AlertDialog(
      title=ft.Text(message, text_align="center"),
      actions=[ft.ElevatedButton("Close", on_click=close_dlg, width=200)],
      actions_alignment="center",
    )

    self.page.dialog = dlg
    dlg.open = True
    self.page.update()

  def no_server(self):
    self.page.clean()
    self.from_room_cleanup()

    self.show_dialog("Cannot connect to the server. Please try again later.", on_close=lambda: self.page.window_close())

  def login(self, *args, **kwargs):
    self.page.clean()
    self.from_room_cleanup()

    def submit(e: ControlEvent, text_username: ft.TextField, text_password: ft.TextField):
      logging.info(f"Login with {text_username.value} (passw: {text_password.value})")

      success, err = self.api.login(username=text_username.value, password=text_password.value)

      if success:
        self.home()
      else:
        logging.error(f"Cannot log in: {err}")
        self.show_dialog(err)

    self.page.add(
      self.title,
      ft.Divider(height=20),
      Text(value="Login", size=30, weight="bold", style=ft.TextStyle(decoration=ft.TextDecoration.UNDERLINE)),
      FormComponent(on_submit=submit),
      ft.TextButton("Register", on_click=self.register)
    )

  def register(self, *args, **kwargs):
    self.page.clean()
    self.from_room_cleanup()

    def submit(e: ControlEvent, text_username: ft.TextField, text_password: ft.TextField):
      logging.info(f"Register with {text_username.value} (passw: {text_password.value})")

      success, err = self.api.register(username=text_username.value, password=text_password.value)

      if success:
        self.home()
      else:
        logging.error(f"Cannot register: {err}")
        self.show_dialog(err)

    self.page.add(
      self.title,
      ft.Divider(height=20),
      Text(value="Register", size=30, weight="bold", style=ft.TextStyle(decoration=ft.TextDecoration.UNDERLINE)),
      FormComponent(on_submit=submit),
      ft.TextButton("Login", on_click=self.login)
    )

  def from_room_cleanup(self):
    self.current_room = None
    self.api.leave_room()

    if pygame.mixer.music.get_busy():
      pygame.mixer.music.stop()
      self.currently_playing = None

  def home(self, *args, **kwargs):
    self.page.clean()
    self.from_room_cleanup()

    def on_room_click(e: ControlEvent, room: int):
      logging.info(f"Enter room {room}")
      self.room(room)

    room_buttons = [
      ft.FilledButton(f"Room {i}", icon=ft.icons.LIBRARY_MUSIC, on_click=functools.partial(on_room_click, room=i),
                      height=80, width=200)
      for i in range(1, 6 + 1)
    ]

    self.page.add(
      self.logged_title,
      ft.Divider(height=20),
      ft.Row(room_buttons[:3], alignment=ft.MainAxisAlignment.SPACE_AROUND, height=self.page.height / 3),
      ft.Row(room_buttons[3:], alignment=ft.MainAxisAlignment.SPACE_AROUND, height=self.page.height / 3),
    )

  def update_room_info(self):
    """
    Update the UI of the room screen when new data is available
    """
    if self.current_room:
      logging.debug("updating room")

      info: RoomInfo = self.api.get_room_info(self.current_room)
      # print(info)

      if self.queue_component:
        self.queue_component.content.controls[2:] = [
          SongCard(song, alignment="start") for song in self.api.get_room_info(self.current_room).queue
        ]
        self.queue_component.content.controls[1].value = self.get_queue_text(info)
        self.queue_component.update()

      if self.listeners_component:
        self.listeners_component.content.controls[2:] = [
          ft.Text(listener, size=20) for listener in info.listeners
        ]
        self.listeners_component.content.controls[1].value = self.get_listeners_text(info)
        self.listeners_component.update()

      if self.current_song_component:
        self.current_song_component.update_song(info)

      if info.current_song.id is None:
        # stop if anything is running
        if pygame.mixer.music.get_busy():
          pygame.mixer.music.stop()

      if info.current_song.id and self.currently_playing != info.current_song:
        js = self.api.get_current_song()
        song_base64 = js['song_base64']

        self.currently_playing = info.current_song
        buffer = io.BytesIO(base64.b64decode(song_base64))
        buffer.seek(0)
        pygame.mixer.music.load(buffer)
        self.currently_playing = info.current_song
        pygame.mixer.music.play()
        pygame.mixer.music.set_pos(info.current_seek)

      # self.room(self.current_room)

  def search_song(self, e: ControlEvent):
    """
    Search for a song and display the results.
    The results come from the Spotify API
    """

    query = self.search_query_ref.current.value

    logging.info(f"Search query: {query}")

    if not query:
      self.search_results_component.controls = [
        ft.Text("Search for something...", color=ft.colors.GREY, size=15, italic=True)
      ]
    else:
      song_results = self.api.search_songs(query)

      if len(song_results) == 0:
        self.search_results_component.controls = [ft.Text("No results", size=15, color=ft.colors.GREY, italic=True)]
      else:
        self.search_results_component.controls = [
          ft.Row(
            [
              ft.IconButton(icon=ft.icons.ADD, tooltip="Add song",
                            on_click=functools.partial(self.add_to_queue, song=song),
                            icon_color="white", bgcolor="green"),
              ft.Container(SongCard(song, alignment="start"), margin=ft.margin.only(right=20, left=20)),
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, scroll=ft.ScrollMode.AUTO)
          for song in song_results
        ]
    #
    self.search_results_component.update()

  def skip_song(self, e: ControlEvent):
    logging.info("Skip song")

    # if current song is loading ⏳, no skip
    info = self.api.get_room_info(self.current_room)

    if info.current_song.title.startswith('⏳'):
      return self.show_dialog("Cannot skip while loading the song")

    self.api.skip_song()

    self.update_room_info()

  def add_to_queue(self, e: ControlEvent, song: Song):
    logging.info(f"Add song to queue: {song.title}")

    info = self.api.get_room_info(self.current_room)

    # if last in queue == current song, dont add
    # if len(info.queue) > 0 and info.queue[-1] == info.current_song:
    #   return self.show_dialog("Cannot add the same song in a row")

    resp = self.api.send_add_to_queue(song)

    if resp.get('error') == 'Song is already in the queue':
      return self.show_dialog("Cannot add the same song in a row")

    self.update_room_info()

  def get_listeners_text(self, info: RoomInfo):
    if len(info.listeners) == 0:
      return "No other listeners"
    elif len(info.listeners) == 1:
      return "1 other listener"
    else:
      return f"{len(info.listeners)} other listeners"

  def get_queue_text(self, info: RoomInfo):
    if len(info.queue) == 0:
      return "No songs in queue"
    elif len(info.queue) == 1:
      return "1 queued song"
    else:
      return f"{len(info.queue)} queued songs"

  def room(self, room: int):
    try:
      self.page.clean()
      self.current_room = room

      self.api.join_room(room)

      info: RoomInfo = self.api.get_room_info(room)

      # print('playing first', info)

      # layout:
      # TOP: current song
      # REST: [LEFT: queue, MIDDLE: add song, RIGHT: listeners]

      self.queue_component = ft.Container(
        ft.Column([
          ft.Text("Queue", size=20, weight="bold", style=ft.TextStyle(decoration=ft.TextDecoration.UNDERLINE)),
          ft.Text(value=self.get_queue_text(info), size=15, color=ft.colors.GREY),
          *[SongCard(song, alignment="start") for song in info.queue]
        ], horizontal_alignment="center", width=self.page.width * 0.15, scroll=ft.ScrollMode.AUTO),
        border_radius=10, border=ft.border.all(2, ft.colors.BLUE), padding=20, height=self.page.height / 2
      )

      self.listeners_component = ft.Container(
        ft.Column([
          ft.Text("Listeners", size=20, weight="bold", style=ft.TextStyle(decoration=ft.TextDecoration.UNDERLINE)),
          ft.Text(value=self.get_listeners_text(info), size=15, color=ft.colors.GREY),
          *[
            ft.Text(listener, size=20) for listener in info.listeners
          ]
        ], horizontal_alignment="center", width=self.page.width * 0.15), border_radius=10,
        border=ft.border.all(2, ft.colors.BLUE), padding=20, height=self.page.height / 2
      )

      self.current_song_component = SongCard(info.current_song, is_main=True, alignment="center")

      self.search_results_component = ft.Column([
        ft.Text("Search for something...", color=ft.colors.GREY, size=15, italic=True),
      ], scroll=ft.ScrollMode.AUTO)

      self.search_query_ref = ft.Ref[ft.TextField]()

      self.page.add(
        self.room_title,
        ft.Divider(height=20),
        self.current_song_component,

        ft.Divider(height=20, color="transparent"),
        ft.Row([
          self.queue_component,
          ft.Column([
            ft.Text("Add song", size=20, weight="bold", style=ft.TextStyle(decoration=ft.TextDecoration.UNDERLINE)),
            ft.Row([
              ft.TextField(label="Search song", width=self.page.width * 0.3, hint_text="Song name",
                           ref=self.search_query_ref),
              IconButton(icon=ft.icons.SEARCH, tooltip="Search", icon_size=30,
                         on_click=self.search_song),
            ], alignment="center"),
            ft.Text("Search results", size=15, color=ft.colors.GREY),
            ft.Container(
              self.search_results_component,
              border=ft.border.all(1, ft.colors.GREY),
              padding=20, height=self.page.height * 0.3,
              width=self.page.width * 0.35
            )
          ], width=self.page.width * 0.4, horizontal_alignment="center"),

          self.listeners_component,

        ], alignment=ft.MainAxisAlignment.SPACE_AROUND, height=self.page.height / 2),
        ft.Divider(height=20, color="transparent"),
        ft.Row([
          ft.Text(f"Room {room}", size=30, weight="bold", color=ft.colors.ORANGE_800),
          IconButton(icon=ft.icons.SKIP_NEXT_OUTLINED, tooltip="Skip song", on_click=self.skip_song, icon_size=30)
        ], alignment="center"),
      )

      if info.current_song.id:
        # if busy stop
        if pygame.mixer.music.get_busy():
          pygame.mixer.music.stop()

        logging.info('loading song from buffer')
        js = self.api.get_current_song()
        song_base64 = js['song_base64']
        # print(song_base64[:50])
        buffer = io.BytesIO(base64.b64decode(song_base64))
        buffer.seek(0)
        pygame.mixer.music.load(buffer)
        self.currently_playing = info.current_song
        pygame.mixer.music.play()
        pygame.mixer.music.set_pos(info.current_seek)

    except Exception as e:
      logging.error(f"Error in room: {e}")
      self.no_server()


def events_listener(client: socket.socket, page: ft.Page, screens: Screens, api: API):
  while True:
    if api.token and screens.current_room is not None:
      # update data
      screens.update_room_info()

    time.sleep(1)


from utils import *


def main():
  """
  Main client - handle socket and load ui
  """

  sock = socket.socket()
  ip = SERVER_IP

  if ip == '0.0.0.0':
    ip = '127.0.0.1'

  port = SERVER_PORT

  try:
    sock.connect((ip, port))
    logging.info(f"Connected to the server {ip}:{port}")
  except Exception as e:
    logging.error(f"Error while trying to connect. Check IP or port -- {ip}:{port}")
    return

  events_thread = None

  def flet_main(page: ft.Page):
    nonlocal events_thread

    page.title = config.get('app', 'name')
    page.vertical_alignment = "start"
    page.horizontal_alignment = "center"
    page.theme_mode = "light"

    api = API(client=sock, page=page)

    screens = Screens(page, api=api)

    screens.login()
    events_thread = threading.Thread(target=events_listener, args=(None, page, screens, api), daemon=True)
    events_thread.start()

  # initialzie UI
  ft.app(target=flet_main)

  logging.info("Closing the client")
  sock.close()


if __name__ == '__main__':
  main()
