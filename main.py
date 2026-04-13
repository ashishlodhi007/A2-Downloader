from flask import Flask, render_template_string, request, jsonify, send_file
from pytubefix import YouTube
import os, uuid, subprocess, threading, time, tempfile
from concurrent.futures import ThreadPoolExecutor

app = Flask(__name__)

# ================= CONFIG =================
DOWNLOAD_PATH = tempfile.gettempdir()

executor = ThreadPoolExecutor(max_workers=6)
lock = threading.Lock()

files = {}
progress = {}
status = {}

# ================= UTIL =================
def wait_for_file(path, timeout=60):
    start = time.time()
    while True:
        if os.path.exists(path) and os.path.getsize(path) > 50000:
            return True
        if time.time() - start > timeout:
            return False
        time.sleep(1)

def set_state(uid, p, s=None):
    with lock:
        progress[uid] = p
        if s:
            status[uid] = s

def clean_temp():
    now = time.time()
    for f in os.listdir(DOWNLOAD_PATH):
        path = os.path.join(DOWNLOAD_PATH, f)
        if os.path.isfile(path) and now - os.path.getmtime(path) > 300:
            try:
                os.remove(path)
            except:
                pass

# ================= FILE SERVE =================
@app.route("/file/<uid>")
def file(uid):
    path = files.get(uid)

    if not path or not os.path.exists(path):
        return "Invalid file", 404

    if not wait_for_file(path):
        return "File not ready", 404

    return send_file(
        path,
        as_attachment=True,
        download_name=os.path.basename(path),
        mimetype="application/octet-stream"
    )

# ================= DOWNLOAD ENGINE =================
def download_task(url, uid, mode):
    try:
        yt = YouTube(url)
        set_state(uid, 5, "fetching")

        # ===== MP3 =====
        if mode == "mp3":
            audio = yt.streams.filter(only_audio=True).order_by("abr").desc().first()
            a_path = audio.download(DOWNLOAD_PATH, filename=f"{uid}_a.mp4")

            out_path = os.path.join(DOWNLOAD_PATH, f"{uid}.mp3")
            set_state(uid, 60, "converting")

            result = subprocess.run([
                "ffmpeg", "-y",
                "-i", a_path,
                "-vn",
                "-ab", "192k",
                "-ar", "44100",
                out_path
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            if result.returncode != 0 or not os.path.exists(out_path):
                print("MP3 Error:", result.stderr.decode())
                set_state(uid, 100, "error")
                return

            files[uid] = out_path
            set_state(uid, 100, "done")
            return

        # ===== VIDEO =====
        target = f"{mode}p"

        video = yt.streams.filter(adaptive=True, only_video=True, res=target).first()
        if not video:
            video = yt.streams.filter(adaptive=True, only_video=True)\
                .order_by("resolution").desc().first()

        set_state(uid, 30, "video")
        v_path = video.download(DOWNLOAD_PATH, filename=f"{uid}_v.mp4")

        audio = yt.streams.filter(only_audio=True).order_by("abr").desc().first()

        set_state(uid, 60, "audio")
        a_path = audio.download(DOWNLOAD_PATH, filename=f"{uid}_a.mp4")

        out_path = os.path.join(DOWNLOAD_PATH, f"{uid}.mp4")
        set_state(uid, 80, "merging")

        result = subprocess.run([
            "ffmpeg", "-y",
            "-i", v_path,
            "-i", a_path,
            "-c:v", "copy",
            "-c:a", "aac",
            "-movflags", "+faststart",
            out_path
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        if result.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) < 50000:
            print("FFmpeg Error:", result.stderr.decode())
            set_state(uid, 100, "error")
            return

        files[uid] = out_path
        set_state(uid, 100, "done")

    except Exception as e:
        print("Error:", e)
        set_state(uid, 100, "error")

# ================= START =================
@app.route("/start", methods=["POST"])
def start():
    clean_temp()

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
HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>A2 Downloader</title>

<style>
body{
    margin:0;
    font-family: Inter, sans-serif;
    background:#0b1220;
    display:flex;
    justify-content:center;
    align-items:center;
    height:100vh;
    color:white;
}

.card{
    width:420px;
    background:#111827;
    padding:24px;
    border-radius:14px;
}

input, select{
    width:100%;
    padding:10px;
    margin:10px 0;
    border-radius:8px;
    border:none;
    background:#020617;
    color:white;
}

button{
    width:100%;
    padding:10px;
    border:none;
    border-radius:8px;
    cursor:pointer;
}

.preview-btn{background:#2563eb;color:white;}
.mp4{background:#16a34a;}
.mp3{background:#f59e0b;}

.progress{
    height:6px;
    background:#22c55e;
    width:0%;
}

.box{
    background:#020617;
    height:6px;
    border-radius:6px;
    margin-top:10px;
}
</style>
</head>

<body>

<div class="card">

<h2>A2 Downloader</h2>

<input id="url" placeholder="Paste YouTube link">

<button class="preview-btn" onclick="loadVideo()">Preview</button>

<select id="q">
<option value="360">360p</option>
<option value="720">720p</option>
<option value="1080">1080p</option>
<option value="1440">2K</option>
<option value="2160">4K</option>
</select>

<div id="preview" style="display:none;">
<iframe id="frame" width="100%" height="200"></iframe>

<div id="status">Ready</div>

<div class="box">
<div class="progress" id="bar"></div>
</div>

<button class="mp4" onclick="download('mp4')">Download MP4</button>
<button class="mp3" onclick="download('mp3')">Download MP3</button>

</div>

</div>

<script>

let videoURL="";

function getID(url){
let m=url.match(/(?:v=|youtu\\.be\\/|\\/)([0-9A-Za-z_-]{11})/);
return m?m[1]:null;
}

function loadVideo(){
let url=document.getElementById("url").value;
let id=getID(url);

if(!id){alert("Invalid link");return;}

videoURL=url;
document.getElementById("preview").style.display="block";
document.getElementById("frame").src="https://www.youtube.com/embed/"+id;
}

function download(type){

fetch("/start",{
method:"POST",
headers:{"Content-Type":"application/json"},
body:JSON.stringify({
url:videoURL,
mode:type==="mp3"?"mp3":document.getElementById("q").value
})
})
.then(r=>r.json())
.then(d=>{

let id=d.id;

let t=setInterval(()=>{

fetch("/progress/"+id)
.then(r=>r.json())
.then(p=>{

document.getElementById("status").innerText=p.status+" "+p.progress+"%";
document.getElementById("bar").style.width=p.progress+"%";

if(p.progress>=100){
clearInterval(t);
window.location="/file/"+id;
}

});

},1000);

});

}

</script>

</body>
</html>
"""

@app.route("/")
def home():
    return render_template_string(HTML)

# ================= RUN =================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)