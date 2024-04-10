import enum
import socket
import struct
import configparser
import sys
from urllib.parse import parse_qs

import json

config = configparser.ConfigParser()
config.read('config.ini')

### PROTOCOL SETTINGS ###
MSG_SIZE_FIELD = config.getint('protocol', 'length_header_size')  # message are always: <SIZE_IN_INT>MESSAGE
MSG_ROUTE_LENGTH = config.getint('protocol', 'length_route')
MSG_SENDER_LENGTH = config.getint('protocol', 'length_sender')
MSG_TYPE_LENGTH = config.getint('protocol', 'length_type')

### SOCKET SETTINGS ###
SERVER_IP = config.get('socket', 'ip')
SERVER_PORT = config.getint('socket', 'port')


### ----------------- ###

class DisconnectedError(Exception):
  """Raised when the other side disconnects"""
  eid = '01'


class BadMessageError(Exception):
  """Raised when given message is not valid (not by the protocol)"""
  eid = '02'


class ResponseGenerateError(Exception):
  """Raised when the side failed to generate response"""
  eid = '03'


class GeneralError(Exception):
  """Raised when generally the message could not be handled"""
  eid = '04'


class MissingArguments(Exception):
  """Raised when the message is missing command arguments"""
  eid = '05'


class OkCheck(Exception):
  """Raised when the other side wants to check if the server is alive"""


class MessageType:
  """
  Message types
  """
  JSON = 0
  RAW = 1
  ERROR = 2


def is_connected(b):
  if b == b'':
    raise DisconnectedError()
  elif b == b'OK':
    raise OkCheck()
  return True


def is_alive(sock: socket.socket):
  """
  Check if socket is alive
  """
  try:
    sock.send(b'OK')
    r = sock.recv(2)
    return r.decode() == 'OK'
  except:
    return False


def fetch_amount(sock: socket.socket, amount: int) -> bytes:
  """
  Fetch a specific amount of bytes from the socket
  """
  data = b''

  while len(data) < amount:
    b = sock.recv(amount - len(data))
    is_connected(b)
    data += b

  return data


def fetch_all(sock: socket.socket) -> bytes:
  """
  Fetch all the message from the socket (size is determined by first 4 bytes)
  """

  # get message length header
  size = fetch_amount(sock, MSG_SIZE_FIELD)
  length = get_message_length(size)

  # get the rest of the message
  msg = fetch_amount(sock, length)

  # print("FETCHALL", msg)

  return msg


def parse_message_by_protocol(msg: bytes) -> dict:
  """
  Parse message by protocol

  Protocol:
    [who 1 byte][type 1 byte][api route 4 bytes][json/raw size header - 5…]
  """


  k = 0

  msg_sender, k = msg[k:MSG_SENDER_LENGTH].decode(), k + MSG_SENDER_LENGTH

  try:
    msg_type, k = int(msg[k:k + MSG_TYPE_LENGTH].decode()), k + MSG_TYPE_LENGTH
  except ValueError:
    print("ERORRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR")
    print(msg)
    print("ERORRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR")
    sys.exit(1)

  msg_route, k = msg[k:k + MSG_ROUTE_LENGTH].decode(), k + MSG_ROUTE_LENGTH
  msg_data = msg[k:]

  d = dict(sender=msg_sender, type=msg_type, route=msg_route)

  if msg_data and msg_type in [MessageType.JSON, MessageType.ERROR]:  # error is also json format
    try:
      msg_data = json.loads(msg_data.decode())
    except json.JSONError:
      raise BadMessageError("Message is not valid json")

  return {**d, 'data': msg_data}


def length_header_send(data):
  """
  Calculate length and set its size to int (4 bytes)
  """
  length = socket.htonl(len(data))  # to network byte order
  length = struct.pack('I', length)  # to 4 bytes format (Integer)

  return length


def get_message_length(data):
  """
  Get length of message from length header
  """
  length, = struct.unpack('I', data[:MSG_SIZE_FIELD])  # to 4 bytes format (Integer)
  length = socket.ntohl(length)  # to host byte order
  return length
