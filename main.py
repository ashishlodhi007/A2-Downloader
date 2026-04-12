from flask import Flask, render_template_string, request, jsonify, send_file
from pytubefix import YouTube
import os, uuid, subprocess, threading
from concurrent.futures import ThreadPoolExecutor

app = Flask(__name__)

# ================= CONFIG =================
DOWNLOAD_PATH = "downloads"
os.makedirs(DOWNLOAD_PATH, exist_ok=True)

executor = ThreadPoolExecutor(max_workers=6)
lock = threading.Lock()

files = {}
progress = {}
status = {}
titles = {}

# ================= STATE =================
def set_state(uid, p, s=None):
    with lock:
        progress[uid] = p
        if s:
            status[uid] = s

# ================= INFO =================
@app.route("/info", methods=["POST"])
def info():
    url = request.json["url"]
    try:
        yt = YouTube(url)
        return jsonify({
            "title": yt.title,
            "thumbnail": yt.thumbnail_url
        })
    except Exception as e:
        return jsonify({"error": str(e)})

# ================= FILE DOWNLOAD =================
@app.route("/file/<uid>")
def file(uid):
    path = files.get(uid)
    if not path or not os.path.exists(path):
        return "File not ready", 404
    return send_file(path, as_attachment=True)

# ================= DOWNLOAD ENGINE =================
def download_task(url, uid, mode):
    try:
        yt = YouTube(url)

        safe_title = "".join(c for c in yt.title if c not in r'\/:*?"<>|')
        titles[uid] = safe_title

        set_state(uid, 5, "fetching")

        # ================= MP3 =================
        if mode == "mp3":
            set_state(uid, 20, "audio")

            audio = yt.streams.filter(only_audio=True)\
                .order_by("abr").desc().first()

            a_path = audio.download(DOWNLOAD_PATH, filename=f"{uid}_a.mp4")

            out_path = os.path.join(DOWNLOAD_PATH, f"{safe_title}.mp3")

            set_state(uid, 60, "converting")

            subprocess.run([
                "ffmpeg", "-y",
                "-i", a_path,
                "-vn",
                "-ab", "192k",
                "-ar", "44100",
                out_path
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            files[uid] = out_path
            set_state(uid, 100, "done")
            return

        # ================= VIDEO =================
        set_state(uid, 10, "quality")

        target = f"{mode}p"

        video = yt.streams.filter(adaptive=True, only_video=True)\
            .order_by("resolution").desc().first()

        exact = yt.streams.filter(adaptive=True, only_video=True, res=target).first()
        if exact:
            video = exact

        set_state(uid, 30, "video")

        v_path = video.download(DOWNLOAD_PATH, filename=f"{uid}_v.mp4")

        set_state(uid, 60, "audio")

        audio = yt.streams.filter(only_audio=True)\
            .order_by("abr").desc().first()

        a_path = audio.download(DOWNLOAD_PATH, filename=f"{uid}_a.mp4")

        set_state(uid, 80, "merging")

        out_path = os.path.join(DOWNLOAD_PATH, f"{safe_title}.mp4")

        subprocess.run([
            "ffmpeg", "-y",
            "-i", v_path,
            "-i", a_path,
            "-c:v", "copy",
            "-c:a", "aac",
            "-movflags", "+faststart",
            out_path
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        files[uid] = out_path
        set_state(uid, 100, "done")

    except Exception as e:
        print("Error:", e)
        set_state(uid, 100, "error")
        files[uid] = None

# ================= START =================
@app.route("/start", methods=["POST"])
def start():
    data = request.json
    url = data["url"]
    mode = data.get("mode", "720")

    uid = str(uuid.uuid4())

    set_state(uid, 0, "queued")

    executor.submit(download_task, url, uid, mode)

    return jsonify({"id": uid})

# ================= PROGRESS =================
@app.route("/progress/<uid>")
def get_progress(uid):
    with lock:
        return jsonify({
            "progress": progress.get(uid, 0),
            "status": status.get(uid, "unknown")
        })

# ================= UI =================
HTML = HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>A2Downloader</title>

<style>
body{
    margin:0;
    font-family: Inter, system-ui, sans-serif;
    background:#0b1220;
    display:flex;
    justify-content:center;
    align-items:center;
    height:100vh;
    color:#e5e7eb;
}

.card{
    width:420px;
    background:#111827;
    padding:24px;
    border-radius:14px;
    border:1px solid #1f2937;
}

input, select{
    display:block;
    width:85%;
    padding:12px;
    margin:10px auto; /* CENTER FIX */
    border-radius:8px;
    border:1px solid #1f2937;
    background:#020617;
    color:#e5e7eb;
    font-size:14px;
}

button{
    border:none;
    border-radius:8px;
    padding:10px;
    font-size:14px;
    font-weight:500;
    cursor:pointer;
    transition:0.2s;
}

/* Centered Preview Button */
.preview-btn{
    display:block;
    width:85%;
    margin:12px auto;
    background:#2563eb;
    color:white;
}

.preview-btn:hover{
    background:#1d4ed8;
}

/* Buttons */
.mp4{
    background:#16a34a;
    color:white;
}

.mp4:hover{
    background:#15803d;
}

.mp3{
    background:#f59e0b;
    color:black;
}

.mp3:hover{
    background:#d97706;
}

/* Preview Section */
.preview{
    display:none;
    margin-top:18px;
}

iframe{
    width:100%;
    height:200px;
    border-radius:8px;
}

.progress-box{
    margin-top:10px;
    height:6px;
    background:#020617;
    border-radius:6px;
    overflow:hidden;
}

.progress{
    height:100%;
    width:0%;
    background:#22c55e;
    transition:0.3s;
}

#title{
    margin-top:10px;
    font-size:13px;
    text-align:center;
    color:#9ca3af;
}
</style>

</head>

<body>

<div class="card">

<div style="text-align:center; margin-bottom:18px;">
    <h1 style="margin:0; font-size:20px; font-weight:600;">
        A2 DOWNLOADER
    </h1>
    <p style="margin:4px 0 0; font-size:13px; color:#9ca3af;">
        Made by Ashish
    </p>
</div>

<input id="url" placeholder="Paste YouTube link">

<button class="preview-btn" onclick="loadVideo()">Preview</button>

<select id="videoQuality">
<option value="360">360p</option>
<option value="480">480p</option>
<option value="720">720p</option>
<option value="1080">1080p</option>
<option value="1440" selected>2K</option>
<option value="2160">4K</option>
<option value="4320">8K</option>
</select>

<div class="preview" id="preview">

<div style="border-radius:10px; overflow:hidden;">
    <iframe id="frame"></iframe>
</div>

<div id="title">Ready</div>

<div class="progress-box">
    <div class="progress" id="bar"></div>
</div>

<div style="display:flex; gap:14px; margin-top:18px;">
    <button class="mp4" onclick="downloadVideo('mp4')" style="flex:1;">
        Download MP4
    </button>

    <button class="mp3" onclick="downloadVideo('mp3')" style="flex:1;">
        Download MP3
    </button>
</div>

</div>

</div>

<script>

let videoURL = "";

function getVideoID(url){
    let m = url.match(/(?:v=|youtu\\.be\\/|\\/)([0-9A-Za-z_-]{11})/);
    return m ? m[1] : null;
}

function loadVideo(){
    let url = document.getElementById("url").value;
    let id = getVideoID(url);

    if(!id){
        alert("Invalid link");
        return;
    }

    videoURL = url;

    document.getElementById("preview").style.display = "block";
    document.getElementById("frame").src =
        "https://www.youtube.com/embed/" + id;

    document.getElementById("title").innerText = "Video loaded";
}

function downloadVideo(type){

    if(!videoURL){
        alert("Load video first");
        return;
    }

    fetch("/start",{
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify({
            url:videoURL,
            mode: type === "mp3"
                ? "mp3"
                : document.getElementById("videoQuality").value
        })
    })
    .then(r => r.json())
    .then(d => {

        let id = d.id;

        let t = setInterval(() => {

            fetch("/progress/" + id)
            .then(r => r.json())
            .then(p => {

                document.getElementById("title").innerText =
                    p.status + " • " + p.progress + "%";

                document.getElementById("bar").style.width =
                    p.progress + "%";

                if(p.progress >= 100){
                    clearInterval(t);
                    window.location = "/file/" + id;
                }

            });

        }, 1000);

    });

}

</script>

</body>
</html>
'''
@app.route("/")
def home():
    return render_template_string(HTML)

# ================= RUN =================
if __name__ == "__main__":
    app.run(debug=True)