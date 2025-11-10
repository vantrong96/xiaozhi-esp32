#!/usr/bin/env python3
"""
MCP tool: xiaozhi_ai_dj
"""

import os
import base64
import tempfile
import json
from typing import Optional, Dict, Any
import requests
import urllib.parse
import re
import subprocess

# MCP framework
try:
    from mcp.server.fastmcp import FastMCP
except Exception:
    raise RuntimeError("Không tìm thấy mcp.server.fastmcp — clone repo 78/mcp-calculator và pip install -e .")

mcp = FastMCP("xiaozhi_ai_dj")

# ===== CONFIG =====
BASE_URL = "http://www.xiaozhishop.xyz:5005"
HTTP_TIMEOUT = 6

# ----------------- Helpers -----------------
def build_pcm_url(song: str, artist: Optional[str] = "") -> str:
    return (
        f"{BASE_URL}/stream_pcm"
        f"?song={urllib.parse.quote(song)}"
        f"&artist={urllib.parse.quote(artist or '')}"
    )

def probe_for_mp3_in_pcm_response(pcm_url: str) -> Optional[str]:
    try:
        r = requests.head(pcm_url, timeout=HTTP_TIMEOUT, allow_redirects=True)
        dump = json.dumps(dict(r.headers))
        m = re.search(r"(/music_cache/[0-9a-f]{32}\.mp3)", dump, flags=re.I)
        if m: return BASE_URL + m.group(1)

        r2 = requests.get(pcm_url, timeout=HTTP_TIMEOUT)
        body = r2.text[:4000] if r2.text else ""
        m = re.search(r"(/music_cache/[0-9a-f]{32}\.mp3)", body, flags=re.I)
        if m: return BASE_URL + m.group(1)
    except:
        pass
    return None

def try_get_mp3_by_search(song: str, artist: Optional[str] = "") -> Optional[str]:
    try:
        params = {"q": song}
        if artist: params["artist"] = artist
        r = requests.get(f"{BASE_URL}/search", params=params, timeout=HTTP_TIMEOUT)
        if r.status_code == 200:
            if "json" in r.headers.get("Content-Type","").lower():
                j = r.json()
                def find(o):
                    if isinstance(o,str) and o.endswith(".mp3"): return o
                    if isinstance(o,dict):
                        for v in o.values():
                            x = find(v)
                            if x: return x
                    if isinstance(o,list):
                        for v in o:
                            x = find(v)
                            if x: return x
                res = find(j)
                if res:
                    return BASE_URL + res if res.startswith("/") else res
            m=re.search(r"(/music_cache/[0-9a-f]{32}\.mp3)",r.text)
            if m:return BASE_URL+m.group(1)
    except:
        pass
    return None

def find_play_url(song: str, artist: Optional[str] = "") -> Dict[str,Any]:
    pcm = build_pcm_url(song,artist)
    mp3 = probe_for_mp3_in_pcm_response(pcm)
    if mp3: return {"type":"mp3","url":mp3,"source":"pcm_probe"}

    mp3 = try_get_mp3_by_search(song,artist)
    if mp3: return {"type":"mp3","url":mp3,"source":"search_api"}

    return {"type":"pcm","url":pcm,"source":"fallback_pcm"}

def call_stt(url: str, path: str) -> Optional[str]:
    try:
        r = requests.post(url, files={"file": open(path,"rb")}, timeout=20)
        if r.status_code==200:
            if "json" in r.headers.get("Content-Type","”).lower():
                j=r.json()
                return j.get("text") or j.get("transcript") or j.get("data")
            else:
                return r.text.strip()
    except:
        pass
    return None

# ----------------- Tools -----------------
@mcp.tool(name="search_music", description="Search a song by text (song name and optional artist) and return a playable URL")
def request_song_text(query:str, artist: Optional[str]=None)->Dict[str,Any]:
    """
    Inputs:
    - query: song name or free-form text
    - artist: optional artist name
    Output:
    - success: bool
    - query, artist: echo inputs
    - playback: { type: 'mp3'|'pcm', url: str, source: str }
    - display_text: string suitable to display on device
    """
    if not isinstance(query, str) or not query.strip():
        return {"success": False, "error": "empty_query"}
    artist = artist or ""
    playback = find_play_url(query.strip(), artist.strip())
    display_text = f"《{query.strip()}》" + (f" - {artist.strip()}" if artist.strip() else "")
    return {
        "success": True,
        "query": query,
        "artist": artist or "",
        "playback": playback,
        "display_text": display_text
    }

@mcp.tool(name="search_music_by_voice", description="Search a song by voice (base64 audio). Returns transcript and a playable URL")
def request_song_voice(audio_b64:str, audio_filename:str="req.wav", stt_url:Optional[str]=None)->Dict[str,Any]:
    """
    Inputs:
    - audio_b64: base64-encoded wav audio (mono, 16k recommended)
    - audio_filename: optional filename hint
    - stt_url: optional external STT endpoint to try first
    Output:
    - success: bool
    - transcript: recognized text (if any)
    - playback: { type: 'mp3'|'pcm', url: str, source: str }
    - debug: details
    - display_text: string suitable to display on device
    """
    try:
        raw=base64.b64decode(audio_b64)
        fd,path=tempfile.mkstemp(suffix=".wav")
        with os.fdopen(fd,"wb") as f: f.write(raw)
    except Exception as e:
        return {"success":False,"error":"invalid_base64","detail":str(e)}

    transcript=None
    debug={"stt":[]}

    if stt_url:
        t=call_stt(stt_url,path); debug["stt"].append({"endpoint":stt_url,"ok":bool(t)})
        if t: transcript=t

    if not transcript:
        s2=f"{BASE_URL}/stt"
        t=call_stt(s2,path); debug["stt"].append({"endpoint":s2,"ok":bool(t)})
        if t: transcript=t

    os.remove(path)

    if not transcript:
        return {"success":False,"error":"stt_fail","debug":debug}

    playback = find_play_url(transcript or "")
    display_text = f"《{transcript}》" if transcript else ""
    return {
        "success": True,
        "transcript": transcript or "",
        "playback": playback,
        "debug": debug,
        "display_text": display_text
    }

@mcp.tool(name="play_mp3_local", description="Download an MP3 and play locally on the host (debug)")
def play_mp3_local(url:str)->Dict[str,Any]:
    try:
        r=requests.get(url,timeout=10)
        if r.status_code!=200:
            return {"success":False,"error":"download_fail", "http":r.status_code}

        tmp=tempfile.NamedTemporaryFile(delete=False,suffix=".mp3")
        tmp.write(r.content); tmp.close()

        try:
            subprocess.Popen(["ffplay","-nodisp","-autoexit",tmp.name])
            player="ffplay"
        except:
            subprocess.Popen(["mpg123",tmp.name])
            player="mpg123"

        return {"success":True,"url":url,"file":tmp.name,"player":player}
    except Exception as e:
        return {"success":False,"error":str(e)}

# ----------------- Run MCP -----------------
if __name__ == "__main__":
    mcp.run(transport="stdio")
