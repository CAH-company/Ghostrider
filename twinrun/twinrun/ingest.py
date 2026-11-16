"""Normalize an uploaded video: HDR->SDR tonemap (iPhone HLG/PQ), downscale, trim, constant 8-bit H.264."""
import json
import subprocess


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,r_frame_rate,color_transfer,nb_frames,duration:format=duration",
         "-of", "json", path], capture_output=True, text=True, check=True).stdout
    j = json.loads(out)
    s = j["streams"][0]
    num, den = s.get("r_frame_rate", "30/1").split("/")
    fps = float(num) / float(den or 1)
    dur = float(s.get("duration") or j.get("format", {}).get("duration") or 0)
    return {"width": s["width"], "height": s["height"], "fps": fps, "duration": dur,
            "hdr": s.get("color_transfer") in ("arib-std-b67", "smpte2084")}


def normalize(src, dst, width=960, start=None, end=None, dst_hi=None, width_hi=1280, fps=None):
    """Tonemap + trim. Writes an analysis copy (width) and optionally a render copy (width_hi)."""
    info = probe(src)
    tm = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
          "tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,") if info["hdr"] else ""
    cmd = ["ffmpeg", "-v", "error", "-y"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", src]
    if end:
        cmd += ["-t", f"{end - (start or 0):.3f}"]
    rate = ["-r", f"{fps:.3f}"] if fps else []
    enc = [*rate, "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-an"]
    if dst_hi:
        fc = (f"[0:v:0]scale={width_hi}:-2:flags=area,{tm}format=yuv420p,split=2[a][b];"
              f"[a]scale={width}:-2:flags=area[lo];[b]null[hi]")
        cmd += ["-filter_complex", fc, "-map", "[lo]", *enc, dst, "-map", "[hi]", *enc, dst_hi]
    else:
        cmd += ["-map", "0:v:0", "-vf", f"scale={width}:-2:flags=area,{tm}format=yuv420p", *enc, dst]
    subprocess.run(cmd, check=True)
    return info


def preview(src, dst, width=640):
    """Small browser-friendly preview (for picking the start frame in the UI)."""
    info = probe(src)
    vf = [f"scale={width}:-2"]
    if info["hdr"]:
        vf.append("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
                  "tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv")
    vf.append("format=yuv420p")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-map", "0:v:0", "-vf", ",".join(vf),
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-g", "1",
                    "-an", "-movflags", "+faststart", dst], check=True)
    return info
