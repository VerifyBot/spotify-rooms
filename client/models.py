import dataclasses

@dataclasses.dataclass()
class Song:
  id: str = None
  title: str = "No song playing"
  artist: str = "why not add one?"
  image_url: str = "https://i.imgur.com/pTjJEDX.png"  # placeholder

  def __eq__(self, other):
    if other is None:
      return False

    return self.title == other.title and self.artist == other.artist


@dataclasses.dataclass()
class RoomInfo:
  listeners: list[str]
  queue: list[Song]
  current_song: Song
  current_seek: int = 0

