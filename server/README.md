# 🎶 Spotify Rooms Server

## 🪜 How to Run

### For initial setup

#### Get Spotify API credentials
1. create a new app [here](https://developer.spotify.com/)
2. set the redirect URI to `http://localhost:4832/callback`
3. enter the Client ID & Client Secret in `config.ini`

#### Run on the terminal:

```shell
pip install -r requirements.txt  # install requirements
python setupdb.py  # set up database
```

### Then to run afterward

```shell
python server.py  # run server
```