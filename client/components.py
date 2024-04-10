import flet as ft
from flet_core.control_event import ControlEvent
from models import Song
import functools

class FormComponent(ft.UserControl):
  def __init__(self, on_submit):
    super().__init__()

    self.on_submit = on_submit

  def build(self):
    text_username = ft.TextField(label='Username', text_align="left", width=300, hint_text='Enter your username')
    text_password = ft.TextField(label='Password', text_align="left", width=300, hint_text='Enter your password',
                                 password=True, can_reveal_password=True)
    button_submit = ft.ElevatedButton(text="Submit", width=300, height=50, disabled=True, on_click=functools.partial(
      self.on_submit, text_username=text_username, text_password=text_password
    ))

    def validate(e: ControlEvent):
      button_submit.disabled = not (text_username.value and text_password.value)
      self.update()

    text_username.on_change = validate
    text_password.on_change = validate

    return ft.Row([
      ft.Column([
        text_username,
        text_password,
        button_submit
      ])
    ], alignment="center")


class SongCard(ft.UserControl):
  """
  A card that displays a song with an image, title and artist.

  :param song: The song to display
  :param main: If True, the card will be bigger
  """

  def __init__(self, song: Song, is_main: bool = False, alignment="start"):
    super().__init__()

    self.song = song
    self.is_main = is_main
    self.alignment = alignment

  def build(self):
    im_size = 50 if not self.is_main else 80
    title_size = 20 if not self.is_main else 30
    artist_size = 15 if not self.is_main else 20

    return ft.Row([
      ft.Image(src=self.song.image_url, width=im_size, height=im_size, fit=ft.ImageFit.CONTAIN, border_radius=10),
      ft.Column([
        ft.Text(self.song.title, size=title_size, weight="bold"),
        ft.Text(self.song.artist, size=artist_size),
      ], alignment="center", spacing=0),
    ], alignment=self.alignment)

  def update_song(self, info):
    self.controls[0].controls[1].controls[0].value = info.current_song.title
    self.controls[0].controls[1].controls[1].value = info.current_song.artist
    self.controls[0].controls[0].src = info.current_song.image_url
    self.update()


class IconButton(ft.IconButton):
  """The default ft.IconButton isn't aligned with the text, so we need to create a custom one."""

  def __init__(self, icon: str, tooltip: str = None, *args, **kwargs):
    super().__init__(
      icon=icon, tooltip=tooltip,
      style=ft.ButtonStyle(padding=ft.padding.only(top=8)),
      *args, **kwargs
    )
