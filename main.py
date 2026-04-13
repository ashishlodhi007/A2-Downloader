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
def wait_for_file(path, timeout=30):
    start = time.time()
    while not os.path.exists(path):
        if time.time() - start > timeout:
            return False
        time.sleep(1)
    return True

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

    if not path:
        return "Invalid file", 404

    if not wait_for_file(path, timeout=60):
        return "File not ready", 404

    response = send_file(path, as_attachment=True)

    @response.call_on_close
    def cleanup():
        try:
            if os.path.exists(path):
                os.remove(path)

            for ext in ["_v.mp4", "_a.mp4"]:
                temp_file = os.path.join(DOWNLOAD_PATH, uid + ext)
                if os.path.exists(temp_file):
                    os.remove(temp_file)

            files.pop(uid, None)
            progress.pop(uid, None)
            status.pop(uid, None)

        except Exception as e:
            print("Cleanup error:", e)

    return response

# ================= DOWNLOAD ENGINE =================
def download_task(url, uid, mode):
    try:
        yt = YouTube(url)

        set_state(uid, 5, "fetching")

        # MP3
        if mode == "mp3":
            audio = yt.streams.filter(only_audio=True)\
                .order_by("abr").desc().first()

            a_path = audio.download(DOWNLOAD_PATH, filename=f"{uid}_a.mp4")

            out_path = os.path.join(DOWNLOAD_PATH, f"{uid}.mp3")

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

        # VIDEO
        target = f"{mode}p"

        video = yt.streams.filter(adaptive=True, only_video=True, res=target).first()
        if not video:
            video = yt.streams.filter(adaptive=True, only_video=True)\
                .order_by("resolution").desc().first()

        set_state(uid, 30, "video")

        v_path = video.download(DOWNLOAD_PATH, filename=f"{uid}_v.mp4")

        audio = yt.streams.filter(only_audio=True)\
            .order_by("abr").desc().first()

        set_state(uid, 60, "audio")

        a_path = audio.download(DOWNLOAD_PATH, filename=f"{uid}_a.mp4")

        set_state(uid, 80, "merging")

        out_path = os.path.join(DOWNLOAD_PATH, f"{uid}.mp4")

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
    margin:10px auto;
    border-radius:8px;
    border:1px solid #1f2937;
    background:#020617;
    color:#e5e7eb;
}

button{
    border:none;
    border-radius:8px;
    padding:10px;
    cursor:pointer;
}

.preview-btn{
    display:block;
    width:85%;
    margin:12px auto;
    background:#2563eb;
    color:white;
}

.mp4{background:#16a34a;color:white;}
.mp3{background:#f59e0b;color:black;}

.preview{display:none;margin-top:18px;}

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
}

.progress{
    height:100%;
    width:0%;
    background:#22c55e;
}
</style>
</head>

<body>

<div class="card">

<h2 style="text-align:center;">A2 Downloader</h2>

<input id="url" placeholder="Paste YouTube link">

<button class="preview-btn" onclick="loadVideo()">Preview</button>

<select id="videoQuality">
<option value="360">360p</option>
<option value="720" selected>720p</option>
<option value="1080">1080p</option>
<option value="1440">2K</option>
<option value="2160">4K</option>
</select>

<div class="preview" id="preview">
<iframe id="frame"></iframe>

<div id="title">Ready</div>

<div class="progress-box">
<div class="progress" id="bar"></div>
</div>

<div style="display:flex;gap:10px;margin-top:10px;">
<button class="mp4" onclick="downloadVideo('mp4')" style="flex:1;">MP4</button>
<button class="mp3" onclick="downloadVideo('mp3')" style="flex:1;">MP3</button>
</div>

</div>

</div>

<script>

let videoURL="";

function getVideoID(url){
let m=url.match(/(?:v=|youtu\\.be\\/|\\/)([0-9A-Za-z_-]{11})/);
return m?m[1]:null;
}

function loadVideo(){
let url=document.getElementById("url").value;
let id=getVideoID(url);

if(!id){alert("Invalid link");return;}

videoURL=url;
document.getElementById("preview").style.display="block";
document.getElementById("frame").src="https://www.youtube.com/embed/"+id;
}

function downloadVideo(type){

fetch("/start",{
method:"POST",
headers:{"Content-Type":"application/json"},
body:JSON.stringify({
url:videoURL,
mode:type==="mp3"?"mp3":document.getElementById("videoQuality").value
})
})
.then(r=>r.json())
.then(d=>{

let id=d.id;

let t=setInterval(()=>{

fetch("/progress/"+id)
.then(r=>r.json())
.then(p=>{

document.getElementById("title").innerText=p.status+" "+p.progress+"%";
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
    app.run(debug=True)