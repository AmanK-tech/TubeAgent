from agent.core.state import VideoMeta
import imageio_ffmpeg
import re
import yt_dlp
from agent.errors import ToolError

def fetch_task(state,fetch_name,input):
    
    

    def extract_url(text):
        youtube_pattern = r'https?://(?:www\.|m\.)?(?:youtube\.com/watch\?v=|youtu\.be/)[\w-]+'
    
        # Search for the first match
        match = re.search(youtube_pattern, text)
        
        if match:
            return match.group(0)
        
        else:
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

        if(is_downloadable(url)):

            # yt-dlp options
            
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
                duration = info.get("duration")  # in seconds
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
        raise ToolError("Exception when calling TasksApi->fetch_task: {e}")

            


