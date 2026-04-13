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
 <meta name="viewport" content="width=device-width, initial-scale=1.0">
 <title>A2Downloader</title>

 <style>
 body{
     margin:0;
     font-family: Inter, system-ui, sans-serif;
     background:#0b1220;
     display:flex;
     justify-content:center;
     align-items:center;
     min-height:100vh;
     color:#e5e7eb;
 }

 .card{
     width:95%;
     max-width:420px;
     background:#111827;
     padding:24px;
     border-radius:14px;
     border:1px solid #1f2937;
     box-shadow:0 10px 30px rgba(0,0,0,0.4);
 }

 /* Input + Preview row */
 .input-group{
     display:flex;
     gap:10px;
     margin-bottom:10px;
 }

 input{
     flex:1;
     padding:12px;
     border-radius:8px;
     border:1px solid #1f2937;
     background:#020617;
     color:#e5e7eb;
 }

 .preview-btn{
     padding:12px;
     border-radius:8px;
     background:#2563eb;
     color:white;
     border:none;
     cursor:pointer;
     white-space:nowrap;
 }

 .preview-btn:hover{
     background:#1d4ed8;
 }

 select{
     width:100%;
     padding:12px;
     margin-top:10px;
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
     transition:0.2s;
 }

 .mp4{background:#16a34a;color:white;}
 .mp3{background:#f59e0b;color:black;}

 .mp4:hover{background:#15803d;}
 .mp3:hover{background:#d97706;}

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
     overflow:hidden;
 }

 .progress{
     height:100%;
     width:0%;
     background:#22c55e;
     transition:0.3s;
 }

 /* Mobile */
 @media(max-width:480px){
     .input-group{
         flex-direction:column;
     }
 }
 </style>
 </head>

 <body>

 <div class="card">

 <h2 style="text-align:center;">A2 Downloader 🚀</h2>

 <div class="input-group">
     <input id="url" placeholder="Paste YouTube link">
     <button class="preview-btn" onclick="loadVideo()">Preview</button>
 </div>

 <select id="videoQuality">
 <option value="360">360p</option>
 <option value="720" selected>720p</option>
 <option value="1080">1080p</option>
  <option value="1440">1440p</option>
 <option value="2160">4K</option>
 </select>

 <div class="preview" id="preview">
 <iframe id="frame"></iframe>

 <div id="title" style="margin-top:8px;">Ready</div>

 <div class="progress-box">
 <div class="progress" id="bar"></div>
 </div>

 <div style="display:flex;gap:10px;margin-top:12px;">
 <button class="mp4" onclick="downloadVideo('mp4')" style="flex:1;">Download MP4</button>
 <button class="mp3" onclick="downloadVideo('mp3')" style="flex:1;">Download MP3</button>
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
<<<<<<< HEAD
    app.run(host="0.0.0.0", port=5000)
=======
    app.run(debug=True)
>>>>>>> 6aa61de (my latest changes)
