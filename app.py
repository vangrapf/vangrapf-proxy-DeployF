#!/usr/bin/env python3
import os
import tempfile
import requests
from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
import yt_dlp

app = Flask(__name__)
CORS(app)

# ---------- Конфигурация ----------
DEFAULT_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"
DEFAULT_TIMEOUT = 30
COOKIE_ENV = os.environ.get("YOUTUBE_COOKIES")

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

def get_cookies_file():
    """Создаёт временный файл из переменной окружения YOUTUBE_COOKIES"""
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

def get_format_string(quality):
    """Возвращает строку формата для yt-dlp в зависимости от запрошенного качества."""
    if quality == "audio":
        return "bestaudio[ext=m4a]/bestaudio/best"
    elif quality == "best":
        return "best[height<=720]/best"
    else:
        try:
            height = int(quality)
            return f"best[height<={height}]/best"
        except ValueError:
            return "best[height<=720]/best"

def get_direct_url(video_url, quality="best"):
    """Извлекает прямую ссылку на видео через yt-dlp"""
    cookie_file = get_cookies_file()
    format_str = get_format_string(quality)
    
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": DEFAULT_TIMEOUT,
        "retries": 5,
        "user_agent": DEFAULT_USER_AGENT,
        "format": format_str,
        "no_playlist": True,
        "noprogress": True,
    }
    if cookie_file:
        ydl_opts["cookiefile"] = cookie_file

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            if not info:
                return None, None, "No info extracted"
            
            direct = info.get("url")
            
            format_info = {
                "format_id": info.get("format_id"),
                "format": info.get("format"),
                "ext": info.get("ext"),
                "height": info.get("height"),
                "width": info.get("width"),
                "fps": info.get("fps"),
                "vcodec": info.get("vcodec"),
                "acodec": info.get("acodec"),
                "abr": info.get("abr"),
                "tbr": info.get("tbr"),
                "filesize": info.get("filesize"),
            }
            
            print(f"[STREAM] quality={quality} format_str='{format_str}' -> "
                  f"format_id={format_info.get('format_id')} "
                  f"height={format_info.get('height')} "
                  f"vcodec={format_info.get('vcodec')} "
                  f"acodec={format_info.get('acodec')}")
            
            if not direct:
                return None, None, "No direct URL found"
            
            return direct, format_info, None
    except Exception as e:
        error_msg = str(e)
        print(f"Extract error: {error_msg}")
        return None, None, error_msg
    finally:
        if cookie_file and os.path.exists(cookie_file):
            try:
                os.remove(cookie_file)
            except:
                pass

def get_all_formats(video_url):
    """Получает все доступные форматы для видео"""
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

# ---------- API ----------
@app.route('/health')
def health():
    return jsonify({"status": "ok", "version": "3.0"})

@app.route('/formats', methods=['POST'])
def formats():
    """Получить все доступные форматы для видео"""
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
        resp = session.get(
            direct_url, 
            headers=headers, 
            stream=True, 
            timeout=DEFAULT_TIMEOUT,
            allow_redirects=True
        )
        
        if resp.status_code == 403:
            print(f"[STREAM] Got 403, retrying without Accept-Encoding...")
            retry_headers = headers.copy()
            retry_headers.pop('Accept-Encoding', None)
            resp = session.get(
                direct_url, 
                headers=retry_headers, 
                stream=True, 
                timeout=DEFAULT_TIMEOUT,
                allow_redirects=True
            )
        
        def generate():
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    yield chunk
        
        response = Response(
            stream_with_context(generate()),
            status=resp.status_code,
            content_type=resp.headers.get('Content-Type', 'video/mp4')
        )
        
        if 'Content-Range' in resp.headers:
            response.headers['Content-Range'] = resp.headers['Content-Range']
        if 'Content-Length' in resp.headers:
            response.headers['Content-Length'] = resp.headers['Content-Length']
        if 'Accept-Ranges' in resp.headers:
            response.headers['Accept-Ranges'] = resp.headers['Accept-Ranges']
        else:
            response.headers['Accept-Ranges'] = 'bytes'
        
        if format_info:
            if format_info.get('height'):
                response.headers['X-Format-Height'] = str(format_info.get('height'))
            if format_info.get('vcodec'):
                response.headers['X-Format-Vcodec'] = format_info.get('vcodec')
            if format_info.get('acodec'):
                response.headers['X-Format-Acodec'] = format_info.get('acodec')
            if format_info.get('format_id'):
                response.headers['X-Format-Id'] = str(format_info.get('format_id'))
        
        return response
        
    except requests.exceptions.Timeout:
        return jsonify({"error": "Timeout while streaming video"}), 504
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Request error: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500

@app.route('/download', methods=['POST'])
def download():
    data = request.get_json()
    if not data or 'url' not in data:
        return jsonify({"error": "Missing url in request body"}), 400
    
    video_url = data['url']
    quality = data.get('quality', 'best')
    
    direct_url, format_info, error = get_direct_url(video_url, quality)
    
    if not direct_url:
        return jsonify({"error": "Failed to extract video URL", "details": error}), 500

    headers = BROWSER_HEADERS.copy()

    try:
        session = requests.Session()
        resp = session.get(direct_url, headers=headers, stream=True, timeout=DEFAULT_TIMEOUT)
        
        def generate():
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    yield chunk
        
        response_headers = {
            "Content-Disposition": "attachment; filename=video.mp4",
        }
        
        if 'Content-Length' in resp.headers:
            response_headers['Content-Length'] = resp.headers['Content-Length']
        
        return Response(
            stream_with_context(generate()),
            headers=response_headers,
            content_type=resp.headers.get('Content-Type', 'video/mp4')
        )
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/info', methods=['POST'])
def info():
    """Получить информацию о видео без стриминга"""
    data = request.get_json()
    if not data or 'url' not in data:
        return jsonify({"error": "Missing url"}), 400
    
    video_url = data['url']
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
            if info:
                return jsonify({
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
                })
            return jsonify({"error": "No info"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if cookie_file and os.path.exists(cookie_file):
            try:
                os.remove(cookie_file)
            except:
                pass

@app.route('/search', methods=['POST'])
def search():
    """Поиск видео на YouTube"""
    data = request.get_json()
    if not data or 'query' not in data:
        return jsonify({"error": "Missing query"}), 400
    
    query = data['query']
    cookie_file = get_cookies_file()
    
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "playlistend": 20,
    }
    if cookie_file:
        ydl_opts["cookiefile"] = cookie_file

    try:
        search_url = f"ytsearch20:{query}"
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(search_url, download=False)
            
            if not info or not info.get("entries"):
                return jsonify({"results": []})
            
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
            
            return jsonify({"results": results})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if cookie_file and os.path.exists(cookie_file):
            try:
                os.remove(cookie_file)
            except:
                pass

@app.route('/trending', methods=['GET'])
def trending():
    """Получить трендовые видео"""
    cookie_file = get_cookies_file()
    
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "playlistend": 20,
    }
    if cookie_file:
        ydl_opts["cookiefile"] = cookie_file

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info("https://www.youtube.com/feed/trending", download=False)
            
            if not info or not info.get("entries"):
                return jsonify({"videos": []})
            
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
            
            return jsonify({"videos": videos})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if cookie_file and os.path.exists(cookie_file):
            try:
                os.remove(cookie_file)
            except:
                pass

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, threaded=True)
