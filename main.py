#!/usr/bin/env python3
"""
Vangrapf Proxy v5.1 – Full-featured proxy with FFmpeg for high-quality streaming.
Supports:
- /watch – watch video in best quality with audio and seeking (Range support)
- /download – download video with audio merged
- /stream – lightweight streaming (may lack audio for high qualities)
- Search, trending, channel, thumbnail, formats, info
- Auto-installation of FFmpeg if missing
"""

import os
import sys
import tempfile
import subprocess
import requests
import tarfile
import shutil
import platform
from pathlib import Path
from flask import Flask, request, jsonify, Response, stream_with_context, send_file, after_this_request
from flask_cors import CORS
import yt_dlp
import isodate

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# ---------- Configuration ----------
DEFAULT_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"
DEFAULT_TIMEOUT = 30
COOKIE_ENV = os.environ.get("YOUTUBE_COOKIES")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")
FFMPEG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ffmpeg")
FFMPEG_BIN = os.path.join(FFMPEG_DIR, "bin", "ffmpeg")
FFMPEG_URL = "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz"

BROWSER_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.youtube.com/",
    "Origin": "https://www.youtube.com",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "video",
    "Sec-Fetch-Mode": "no-cors",
    "Sec-Fetch-Site": "cross-site",
}

# ---------- FFmpeg Auto-Installer ----------
def ensure_ffmpeg():
    """Check if ffmpeg is available; if not, download and extract static build."""
    if os.path.exists(FFMPEG_BIN) and os.access(FFMPEG_BIN, os.X_OK):
        print("[FFmpeg] Found existing ffmpeg binary.")
        return FFMPEG_BIN

    print("[FFmpeg] ffmpeg not found. Attempting to download static build...")
    try:
        os.makedirs(FFMPEG_DIR, exist_ok=True)
        tarball_path = os.path.join(FFMPEG_DIR, "ffmpeg.tar.xz")
        print(f"[FFmpeg] Downloading from {FFMPEG_URL} ...")
        resp = requests.get(FFMPEG_URL, stream=True)
        resp.raise_for_status()
        with open(tarball_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        print("[FFmpeg] Download complete. Extracting...")
        with tarfile.open(tarball_path, 'r:xz') as tar:
            tar.extractall(FFMPEG_DIR)
        extracted_dirs = [d for d in os.listdir(FFMPEG_DIR) if d.startswith("ffmpeg-") and os.path.isdir(os.path.join(FFMPEG_DIR, d))]
        if extracted_dirs:
            src_dir = os.path.join(FFMPEG_DIR, extracted_dirs[0])
            bin_dir = os.path.join(FFMPEG_DIR, "bin")
            os.makedirs(bin_dir, exist_ok=True)
            ffmpeg_src = os.path.join(src_dir, "ffmpeg")
            if os.path.exists(ffmpeg_src):
                shutil.move(ffmpeg_src, os.path.join(bin_dir, "ffmpeg"))
                os.chmod(os.path.join(bin_dir, "ffmpeg"), 0o755)
                print(f"[FFmpeg] Installed to {FFMPEG_BIN}")
                shutil.rmtree(src_dir)
                os.remove(tarball_path)
                return FFMPEG_BIN
        print("[FFmpeg] Extraction failed or binary not found.")
        return None
    except Exception as e:
        print(f"[FFmpeg] Error installing ffmpeg: {e}")
        return None

FFMPEG_PATH = ensure_ffmpeg()
if FFMPEG_PATH:
    print(f"[FFmpeg] Using ffmpeg at: {FFMPEG_PATH}")
else:
    print("[FFmpeg] WARNING: ffmpeg not available. High-quality downloads may fail.")

def get_cookies_file():
    if not COOKIE_ENV:
        return None
    try:
        fd, path = tempfile.mkstemp(suffix=".txt", text=True)
        with os.fdopen(fd, 'w') as f:
            f.write(COOKIE_ENV)
        return path
    except Exception as e:
        print(f"Cookie file error: {e}")
        return None

def download_video_with_ffmpeg(video_url, quality="best"):
    """Download video and merge audio using ffmpeg. Returns (filename, temp_dir)."""
    cookie_file = get_cookies_file()
    temp_dir = tempfile.mkdtemp()
    output_template = os.path.join(temp_dir, '%(title)s.%(ext)s')

    if quality == "best":
        format_str = "bestvideo+bestaudio/best"
    else:
        try:
            height = int(quality)
            format_str = f"bestvideo[height<={height}]+bestaudio/best[height<={height}]"
        except ValueError:
            format_str = "bestvideo+bestaudio/best"

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": DEFAULT_TIMEOUT,
        "retries": 5,
        "user_agent": DEFAULT_USER_AGENT,
        "format": format_str,
        "outtmpl": output_template,
        "merge_output_format": "mp4",
        "no_playlist": True,
        "noprogress": True,
    }
    if cookie_file:
        ydl_opts["cookiefile"] = cookie_file
    if FFMPEG_PATH:
        ydl_opts["ffmpeg_location"] = FFMPEG_PATH

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            filename = ydl.prepare_filename(info)
            if not os.path.exists(filename):
                for f in os.listdir(temp_dir):
                    if f.startswith(os.path.basename(filename).split('.')[0]):
                        filename = os.path.join(temp_dir, f)
                        break
            return filename, temp_dir
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise e
    finally:
        if cookie_file and os.path.exists(cookie_file):
            try:
                os.remove(cookie_file)
            except:
                pass

def get_direct_url(video_url, quality="best"):
    """Extract direct video URL (single stream) for lightweight streaming."""
    cookie_file = get_cookies_file()
    attempts = []
    try:
        height = int(quality)
        attempts.extend([
            f"best[ext=mp4][height<={height}][acodec!=none]",
            f"best[height<={height}]",
        ])
    except ValueError:
        pass
    attempts.extend([
        "best[ext=mp4][vcodec^=avc1][acodec^=mp4a]",
        "best[ext=mp4][acodec!=none]",
        "best[ext=mp4]",
        "best[acodec!=none]",
        "best",
        "worst",
    ])
    attempts = list(dict.fromkeys(attempts))

    last_error = None
    for fmt in attempts:
        try:
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "socket_timeout": DEFAULT_TIMEOUT,
                "retries": 5,
                "user_agent": DEFAULT_USER_AGENT,
                "format": fmt,
                "no_playlist": True,
                "noprogress": True,
            }
            if cookie_file:
                ydl_opts["cookiefile"] = cookie_file
            if FFMPEG_PATH:
                ydl_opts["ffmpeg_location"] = FFMPEG_PATH

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=False)
                if not info:
                    continue
                direct = info.get("url")
                if direct:
                    format_info = {
                        "format_id": info.get("format_id"),
                        "ext": info.get("ext"),
                        "height": info.get("height"),
                        "vcodec": info.get("vcodec"),
                        "acodec": info.get("acodec"),
                    }
                    return direct, format_info, None
        except Exception as e:
            last_error = str(e)
            continue
    return None, None, f"All formats failed. Last error: {last_error}"

# ---------- Helper functions for metadata, search, channel, trending, thumbnail ----------
def get_all_formats(video_url):
    cookie_file = get_cookies_file()
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": DEFAULT_TIMEOUT,
        "no_playlist": True,
    }
    if cookie_file:
        ydl_opts["cookiefile"] = cookie_file
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            if not info or not info.get("formats"):
                return None, "No formats found"
            formats = []
            for f in info["formats"]:
                if f.get("url"):
                    formats.append({
                        "format_id": f.get("format_id"),
                        "ext": f.get("ext"),
                        "resolution": f.get("resolution") or f"{f.get('width', 0)}x{f.get('height', 0)}",
                        "height": f.get("height"),
                        "fps": f.get("fps"),
                        "vcodec": f.get("vcodec"),
                        "acodec": f.get("acodec"),
                        "abr": f.get("abr"),
                        "tbr": f.get("tbr"),
                        "filesize": f.get("filesize"),
                        "format_note": f.get("format_note"),
                        "has_audio": f.get("acodec") not in (None, "none"),
                        "has_video": f.get("vcodec") not in (None, "none"),
                    })
            return formats, None
    except Exception as e:
        return None, str(e)
    finally:
        if cookie_file and os.path.exists(cookie_file):
            try:
                os.remove(cookie_file)
            except:
                pass

def get_video_metadata(video_url):
    cookie_file = get_cookies_file()
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    }
    if cookie_file:
        ydl_opts["cookiefile"] = cookie_file
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            if not info:
                return None
            return {
                "title": info.get("title"),
                "duration": info.get("duration"),
                "thumbnail": info.get("thumbnail"),
                "uploader": info.get("uploader"),
                "uploader_id": info.get("uploader_id"),
                "uploader_url": info.get("uploader_url"),
                "view_count": info.get("view_count"),
                "like_count": info.get("like_count"),
                "description": (info.get("description") or "")[:1000],
                "upload_date": info.get("upload_date"),
                "categories": info.get("categories"),
            }
    except Exception as e:
        print(f"Metadata error: {e}")
        return None
    finally:
        if cookie_file and os.path.exists(cookie_file):
            try:
                os.remove(cookie_file)
            except:
                pass

def search_videos_ytdlp(query, max_results=20):
    cookie_file = get_cookies_file()
    search_query = f"ytsearch{max_results}:{query}"
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
    }
    if cookie_file:
        ydl_opts["cookiefile"] = cookie_file
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(search_query, download=False)
            if not info or not info.get("entries"):
                return []
            results = []
            for entry in info["entries"]:
                results.append({
                    "id": entry.get("id"),
                    "url": entry.get("url") or entry.get("webpage_url"),
                    "title": entry.get("title"),
                    "duration": entry.get("duration"),
                    "uploader": entry.get("uploader") or entry.get("channel"),
                    "thumbnail": entry.get("thumbnail"),
                    "view_count": entry.get("view_count"),
                })
            return results
    except Exception as e:
        print(f"Search error (yt-dlp): {e}")
        return []
    finally:
        if cookie_file and os.path.exists(cookie_file):
            try:
                os.remove(cookie_file)
            except:
                pass

def search_videos_api(query, api_key, max_results=20):
    if not api_key:
        return []
    url = "https://www.googleapis.com/youtube/v3/search"
    params = {
        "part": "snippet",
        "q": query,
        "maxResults": max_results,
        "type": "video",
        "key": api_key,
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code != 200:
            print(f"YouTube API error: {resp.status_code} - {resp.text}")
            return []
        data = resp.json()
        results = []
        for item in data.get("items", []):
            video_id = item["id"]["videoId"]
            snippet = item["snippet"]
            results.append({
                "id": video_id,
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "title": snippet["title"],
                "duration": None,
                "uploader": snippet["channelTitle"],
                "thumbnail": snippet["thumbnails"]["medium"]["url"],
                "view_count": None,
            })
        return results
    except Exception as e:
        print(f"Search API error: {e}")
        return []

def get_channel_videos(channel_id, max_results=20):
    cookie_file = get_cookies_file()
    if channel_id.startswith("UC"):
        channel_url = f"https://www.youtube.com/channel/{channel_id}"
    else:
        channel_url = f"https://www.youtube.com/@{channel_id}"
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "playlistend": max_results,
        "skip_download": True,
    }
    if cookie_file:
        ydl_opts["cookiefile"] = cookie_file
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(channel_url, download=False)
            if not info or not info.get("entries"):
                return []
            videos = []
            for entry in info["entries"]:
                if entry:
                    videos.append({
                        "id": entry.get("id"),
                        "title": entry.get("title"),
                        "url": f"https://www.youtube.com/watch?v={entry.get('id')}",
                        "thumbnail": entry.get("thumbnail"),
                        "duration": entry.get("duration"),
                        "uploader": entry.get("uploader") or info.get("uploader"),
                        "view_count": entry.get("view_count"),
                        "upload_date": entry.get("upload_date"),
                    })
            return videos
    except Exception as e:
        print(f"Channel error: {e}")
        return []
    finally:
        if cookie_file and os.path.exists(cookie_file):
            try:
                os.remove(cookie_file)
            except:
                pass

def get_trending_ytdlp(country_code="RU", max_results=20):
    cookie_file = get_cookies_file()
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "playlistend": max_results,
        "skip_download": True,
    }
    if cookie_file:
        ydl_opts["cookiefile"] = cookie_file
    try:
        url = "https://www.youtube.com/feed/trending"
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info or not info.get("entries"):
                return []
            videos = []
            for entry in info["entries"]:
                videos.append({
                    "id": entry.get("id"),
                    "url": entry.get("url") or entry.get("webpage_url"),
                    "title": entry.get("title"),
                    "duration": entry.get("duration"),
                    "uploader": entry.get("uploader") or entry.get("channel"),
                    "thumbnail": entry.get("thumbnail"),
                    "view_count": entry.get("view_count"),
                })
            return videos
    except Exception as e:
        print(f"Trending error (yt-dlp): {e}")
        return []
    finally:
        if cookie_file and os.path.exists(cookie_file):
            try:
                os.remove(cookie_file)
            except:
                pass

def get_trending_api(api_key, country_code="RU", max_results=20):
    if not api_key:
        return []
    url = "https://www.googleapis.com/youtube/v3/videos"
    params = {
        "part": "snippet,contentDetails",
        "chart": "mostPopular",
        "regionCode": country_code,
        "maxResults": max_results,
        "key": api_key,
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code != 200:
            print(f"Trending API error: {resp.status_code} - {resp.text}")
            return []
        data = resp.json()
        videos = []
        for item in data.get("items", []):
            video_id = item["id"]
            snippet = item["snippet"]
            duration_str = item["contentDetails"]["duration"]
            try:
                duration = int(isodate.parse_duration(duration_str).total_seconds())
            except:
                duration = None
            videos.append({
                "id": video_id,
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "title": snippet["title"],
                "duration": duration,
                "uploader": snippet["channelTitle"],
                "thumbnail": snippet["thumbnails"]["medium"]["url"],
                "view_count": None,
            })
        return videos
    except Exception as e:
        print(f"Trending API error: {e}")
        return []

def proxy_thumbnail(thumbnail_url):
    if not thumbnail_url:
        return None, None
    try:
        resp = requests.get(thumbnail_url, headers={"User-Agent": DEFAULT_USER_AGENT}, stream=True)
        if resp.status_code == 200:
            content_type = resp.headers.get("Content-Type", "image/jpeg")
            return resp.content, content_type
        return None, None
    except:
        return None, None

# ---------- API Endpoints ----------

@app.route('/')
def root():
    return jsonify({
        "name": "Vangrapf Proxy",
        "status": "online",
        "version": "5.1",
        "description": "Proxy server with FFmpeg integration for high-quality streaming and downloads.",
        "ffmpeg_status": "available" if FFMPEG_PATH else "unavailable",
        "endpoints": {
            "/health": "GET - Health check",
            "/search": "POST - Search videos (body: {'query': '...', 'api_key': '...', 'use_api': true/false})",
            "/info": "POST - Get video metadata (body: {'url': '...'})",
            "/formats": "POST - Get all formats (body: {'url': '...'})",
            "/stream": "GET - Lightweight streaming (params: url, quality) – may lack audio for high qualities",
            "/watch": "GET - Watch with best quality and audio (params: url, quality) – supports seeking (Range)",
            "/download": "POST - Download video with audio merged (body: {'url': '...', 'quality': 'best'})",
            "/channel": "POST - Get channel videos (body: {'id': '...'})",
            "/trending": "GET - Get trending videos (params: country, max_results, api_key, use_api)",
            "/thumbnail": "GET - Proxy thumbnail (params: url)"
        }
    })

@app.route('/health')
def health():
    return jsonify({
        "status": "ok",
        "version": "5.1",
        "name": "Vangrapf Proxy",
        "ffmpeg": "found" if FFMPEG_PATH else "missing"
    })

@app.route('/search', methods=['POST'])
def search():
    data = request.get_json()
    if not data or 'query' not in data:
        return jsonify({"error": "Missing query"}), 400
    query = data['query']
    api_key = data.get('api_key') or YOUTUBE_API_KEY
    use_api = data.get('use_api', False)
    max_results = data.get('max_results', 20)
    if use_api and api_key:
        results = search_videos_api(query, api_key, max_results)
    else:
        results = search_videos_ytdlp(query, max_results)
    return jsonify({"results": results})

@app.route('/info', methods=['POST'])
def info():
    data = request.get_json()
    if not data or 'url' not in data:
        return jsonify({"error": "Missing url"}), 400
    video_url = data['url']
    metadata = get_video_metadata(video_url)
    if not metadata:
        return jsonify({"error": "Failed to fetch metadata"}), 500
    return jsonify(metadata)

@app.route('/formats', methods=['POST'])
def formats():
    data = request.get_json()
    if not data or 'url' not in data:
        return jsonify({"error": "Missing url"}), 400
    video_url = data['url']
    formats_list, error = get_all_formats(video_url)
    if not formats_list:
        return jsonify({"error": "Failed to get formats", "details": error}), 500
    return jsonify({"formats": formats_list})

@app.route('/stream')
def stream():
    """Lightweight streaming – may lack audio for high qualities."""
    video_url = request.args.get('url')
    quality = request.args.get('quality', 'best')
    if not video_url:
        return jsonify({"error": "Missing url parameter"}), 400

    direct_url, format_info, error = get_direct_url(video_url, quality)
    if not direct_url:
        return jsonify({"error": "Failed to extract video URL", "details": error}), 500

    headers = BROWSER_HEADERS.copy()
    range_header = request.headers.get('Range')
    if range_header:
        headers['Range'] = range_header

    try:
        session = requests.Session()
        resp = session.get(direct_url, headers=headers, stream=True, timeout=DEFAULT_TIMEOUT, allow_redirects=True)
        if resp.status_code == 403:
            retry_headers = headers.copy()
            retry_headers.pop('Accept-Encoding', None)
            resp = session.get(direct_url, headers=retry_headers, stream=True, timeout=DEFAULT_TIMEOUT, allow_redirects=True)

        def generate():
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    yield chunk

        content_type = resp.headers.get('Content-Type', 'video/mp4')
        if format_info and format_info.get('ext') == 'mp4':
            content_type = 'video/mp4'

        response = Response(stream_with_context(generate()), status=resp.status_code, content_type=content_type)
        if 'Content-Range' in resp.headers:
            response.headers['Content-Range'] = resp.headers['Content-Range']
        if 'Content-Length' in resp.headers:
            response.headers['Content-Length'] = resp.headers['Content-Length']
        response.headers['Accept-Ranges'] = 'bytes'
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Expose-Headers'] = 'Content-Range, Content-Length, Accept-Ranges'
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return response
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/watch', methods=['GET'])
def watch():
    """
    Watch video with best quality and audio.
    Downloads the full video on server and then streams with Range support.
    Suitable for high-quality playback in browser.
    """
    video_url = request.args.get('url')
    quality = request.args.get('quality', 'best')
    if not video_url:
        return jsonify({"error": "Missing url parameter"}), 400

    if not FFMPEG_PATH:
        return jsonify({"error": "FFmpeg not available. Cannot merge audio for watch."}), 503

    try:
        filename, temp_dir = download_video_with_ffmpeg(video_url, quality)
        if not filename or not os.path.exists(filename):
            return jsonify({"error": "Failed to download video"}), 500

        @after_this_request
        def cleanup(response):
            try:
                os.remove(filename)
                os.rmdir(temp_dir)
            except Exception as e:
                print(f"Cleanup error: {e}")
            return response

        # Send file with Range support (for seeking)
        return send_file(filename, as_attachment=False, download_name='video.mp4')
    except Exception as e:
        return jsonify({"error": f"Watch failed: {str(e)}"}), 500

@app.route('/download', methods=['POST'])
def download():
    data = request.get_json()
    if not data or 'url' not in data:
        return jsonify({"error": "Missing url"}), 400
    video_url = data['url']
    quality = data.get('quality', 'best')

    if not FFMPEG_PATH:
        return jsonify({"error": "FFmpeg not available. Cannot merge audio."}), 503

    try:
        filename, temp_dir = download_video_with_ffmpeg(video_url, quality)
        if not filename or not os.path.exists(filename):
            return jsonify({"error": "Download failed"}), 500

        @after_this_request
        def cleanup(response):
            try:
                os.remove(filename)
                os.rmdir(temp_dir)
            except Exception as e:
                print(f"Cleanup error: {e}")
            return response

        return send_file(filename, as_attachment=True, download_name='video.mp4')
    except Exception as e:
        return jsonify({"error": f"Download failed: {str(e)}"}), 500

@app.route('/channel', methods=['POST'])
def channel():
    data = request.get_json()
    if not data or 'id' not in data:
        return jsonify({"error": "Missing channel id"}), 400
    channel_id = data['id']
    max_results = data.get('max_results', 20)
    videos = get_channel_videos(channel_id, max_results)
    return jsonify({"videos": videos})

@app.route('/trending', methods=['GET'])
def trending():
    country = request.args.get('country', 'RU')
    max_results = int(request.args.get('max_results', 20))
    api_key = request.args.get('api_key') or YOUTUBE_API_KEY
    use_api = request.args.get('use_api', 'false').lower() == 'true'
    if use_api and api_key:
        videos = get_trending_api(api_key, country, max_results)
    else:
        videos = get_trending_ytdlp(country, max_results)
    return jsonify({"videos": videos})

@app.route('/thumbnail', methods=['GET'])
def thumbnail():
    url = request.args.get('url')
    if not url:
        return jsonify({"error": "Missing url"}), 400
    content, content_type = proxy_thumbnail(url)
    if content is None:
        return jsonify({"error": "Failed to fetch thumbnail"}), 404
    return Response(content, content_type=content_type)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"[*] Vangrapf Proxy v5.1 starting on port {port}...")
    print(f"[*] FFmpeg status: {'available' if FFMPEG_PATH else 'not available'}")
    print(f"[*] Use /watch?url=...&quality=1080 for high-quality streaming in browser.")
    app.run(host='0.0.0.0', port=port, threaded=True)