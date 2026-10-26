"""Vocaux : copie à la demande par wa-snapshot, puis transcription locale avec whisper."""
import glob
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

CACHE = Path("~/Library/Caches/whatsapp-mcp").expanduser()
MEDIA_DIR = CACHE / "media"
TRANSCRIPTS_DIR = CACHE / "transcripts"
REQUEST = CACHE / "media-request.txt"
LAUNCHD_LABEL = "com.whatsapp-mcp.snapshot"
EXTRA_PATH = ["/opt/homebrew/bin", "/usr/local/bin"]


def transcript_path(media_path: str, transcripts_dir: Path = TRANSCRIPTS_DIR) -> Path:
    # le nom du fichier WhatsApp est un UUID : unique et stable
    return transcripts_dir / (Path(media_path).stem + ".txt")


def read_transcript(media_path: str, transcripts_dir: Path = TRANSCRIPTS_DIR) -> str | None:
    p = transcript_path(media_path, transcripts_dir)
    return p.read_text().strip() if p.exists() else None


def save_transcript(media_path: str, text: str, transcripts_dir: Path = TRANSCRIPTS_DIR) -> None:
    transcripts_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    p = transcript_path(media_path, transcripts_dir)
    p.write_text(text.strip() + "\n")
    p.chmod(0o600)


def fetch_media(rel_paths: list[str], wait: float = 30) -> dict[str, Path | None]:
    """Demande à wa-snapshot (seul à avoir l'accès au dossier WhatsApp) de copier ces vocaux."""
    result = {rel: MEDIA_DIR / rel for rel in rel_paths}
    missing = [rel for rel, p in result.items() if not p.exists()]
    if missing:
        pending = set(REQUEST.read_text().split()) if REQUEST.exists() else set()
        tmp = REQUEST.with_suffix(".tmp")
        tmp.write_text("\n".join(sorted(pending | set(missing))) + "\n")
        tmp.replace(REQUEST)
        subprocess.run(["launchctl", "kickstart", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"],
                       capture_output=True, check=False)
        deadline = time.time() + wait + 0.2 * len(missing)
        # wa-snapshot supprime la demande une fois traitée
        while time.time() < deadline and REQUEST.exists():
            time.sleep(0.3)
    return {rel: (p if p.exists() else None) for rel, p in result.items()}


def find_whisper() -> str | None:
    found = shutil.which("whisper", path=os.pathsep.join([os.environ.get("PATH", ""), *EXTRA_PATH]))
    if found:
        return found
    candidates = glob.glob("/Library/Frameworks/Python.framework/Versions/*/bin/whisper")
    return sorted(candidates)[-1] if candidates else None


def transcribe(paths: list[Path], model: str = "small", language: str | None = None) -> dict[Path, str]:
    """Transcrit en un seul appel à whisper (le modèle n'est chargé qu'une fois)."""
    whisper = find_whisper()
    if not whisper:
        raise FileNotFoundError(
            "whisper introuvable. Installez-le avec `pip install openai-whisper` (ffmpeg est aussi requis)."
        )
    env = {**os.environ, "PATH": os.pathsep.join([*EXTRA_PATH, os.environ.get("PATH", "")])}
    with tempfile.TemporaryDirectory() as out:
        cmd = [whisper, *map(str, paths), "--model", model, "--output_format", "txt",
               "--output_dir", out, "--fp16", "False", "--verbose", "False"]
        if language:
            cmd += ["--language", language]
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
        texts = {}
        for p in paths:
            txt = Path(out) / (p.stem + ".txt")
            if txt.exists():
                texts[p] = " ".join(txt.read_text().split())
        if not texts and proc.returncode != 0:
            raise RuntimeError(f"whisper a échoué : {proc.stderr.strip()[-500:]}")
        return texts
