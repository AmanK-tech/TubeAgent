from agent.core.state import VideoMeta
import re
import yt_dlp
from agent.errors import ToolError
from urllib.parse import urlparse, parse_qs, urlencode

def fetch_task(state,fetch_name,input):
    

    def extract_url(text: str) -> str | None:
        """
        Extract and normalize the first YouTube URL from a string.

        Returns:
            str: Canonical YouTube watch URL (e.g. https://www.youtube.com/watch?v=ID[&t=xx])
            None: if no valid YouTube URL found
        """
        # Regex to find any URL in text
        url_pattern = r'(https?://[^\s]+|www\.[^\s]+)'
        matches = re.findall(url_pattern, text)

        for url in matches:
            # Ensure scheme
            if url.startswith("www."):
                url = "https://" + url

            parsed = urlparse(url)

            # Accept only youtube domains
            if not any(host in parsed.netloc for host in ["youtube.com", "youtu.be"]):
                continue

            video_id = None
            timestamp = None

            # Case 1: normal watch URL → youtube.com/watch?v=VIDEO_ID
            if "youtube.com" in parsed.netloc and parsed.path == "/watch":
                qs = parse_qs(parsed.query)
                if "v" in qs:
                    video_id = qs["v"][0]
                if "t" in qs:
                    timestamp = qs["t"][0]

            # Case 2: youtu.be short link → youtu.be/VIDEO_ID
            elif "youtu.be" in parsed.netloc:
                video_id = parsed.path.lstrip("/")
                qs = parse_qs(parsed.query)
                if "t" in qs:
                    timestamp = qs["t"][0]

            # Case 3: embed or /v/ links
            elif "youtube.com" in parsed.netloc and parsed.path.startswith(("/embed/", "/v/")):
                video_id = parsed.path.split("/")[-1]
                qs = parse_qs(parsed.query)
                if "t" in qs:
                    timestamp = qs["t"][0]

            if video_id:
                # Rebuild canonical URL
                base_url = f"https://www.youtube.com/watch?v={video_id}"
                if timestamp:
                    return f"{base_url}&t={timestamp}"
                return base_url

        return None
    
    
    
    def is_downloadable(url: str) -> bool:

        ydl_opts = {
            'quiet': True,
            'skip_download': True,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.extract_info(url, download=False)
            return True
        except Exception as e:
            return False
        
    try:
        
        url = extract_url(input)
        if url is None: raise ToolError("Invalid YouTube URL: expected youtube.com or youtu.be")

        if is_downloadable(url):

            #  yt-dlp options
            
            ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # Extract info without downloading
                info = ydl.extract_info(url, download=False)

                # Metadata variables
                title = info.get("title")
                source_url = info.get("webpage_url")
                duration = int(info.get("duration"))  # in seconds
                video_id = info.get("id")


                state.video = VideoMeta(


                    video_id = video_id,
                    title = title,
                    duration_s = duration,
                    source_url = source_url

                )
                return state.video
        else:
            raise ToolError("Video not downloadable")

    except Exception as e:
        raise ToolError(f"Exception when calling TasksApi->fetch_task: {e}")

            


