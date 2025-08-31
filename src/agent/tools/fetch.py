from agent.core.state import VideoMeta
from google import genai
from google.genai import types
import yt_dlp
import imageio_ffmpeg

system_prompt = """
You are a specialized YouTube URL extraction tool. Your sole purpose is to identify and extract YouTube URLs from user input text.

## Instructions:
1. **Primary Task**: Scan the input text for any YouTube URLs
2. **URL Formats to Detect**:
   - https://www.youtube.com/watch?v=VIDEO_ID
   - https://youtube.com/watch?v=VIDEO_ID
   - https://youtu.be/VIDEO_ID
   - https://m.youtube.com/watch?v=VIDEO_ID
   - https://www.youtube.com/embed/VIDEO_ID
   - https://www.youtube.com/v/VIDEO_ID
   - YouTube URLs with additional parameters (timestamps, playlists, etc.)

3. **Output Format**: 
   - If YouTube URL found: Return ONLY the clean YouTube URL (preferably in watch format)
   - If multiple URLs found: Return give ONLY ONE url for extracting.
   - If no YouTube URL found: Return "NO_YOUTUBE_URL_FOUND"

4. **URL Cleaning**:
   - Convert youtu.be links to full youtube.com/watch format
   - Remove unnecessary parameters but keep essential ones like video ID
   - Preserve timestamp parameters (&t= or #t=) if present

5. **Examples**:
   Input: "Check out this video https://youtu.be/dQw4w9WgXcQ it's amazing!"
   Output: https://www.youtube.com/watch?v=dQw4w9WgXcQ

   Input: "Please analyze https://www.youtube.com/watch?v=i6pmojRvnFc&t=120s for me"
   Output: https://www.youtube.com/watch?v=i6pmojRvnFc&t=120s

   Input: "I need help with my homework today"
   Output: NO_YOUTUBE_URL_FOUND

## Important Rules:
- Do NOT provide summaries, analysis, or any other content
- Do NOT engage with the user's request beyond URL extraction
- Do NOT explain what you're doing or provide additional commentary
- Focus ONLY on accurate URL identification and extraction
- Return the result immediately without additional formatting or explanation
"""

def fetch_task(state,fetch_name,input):
    key = state.config.api_key         
    
    client = genai.Client(api_key=key)

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=input,

        config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_budget=0), 
            system_instruction= system_prompt
        )
    )

    
    url = response
    
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
            print(f"{response}")
            return False
        
    
    is_downloadable(url)


    # yt-dlp options
    ydl_opts = {
        'ffmpeg_location': '/Users/khatri/TubeAgent/venv/lib/python3.13/site-packages/imageio_ffmpeg/binaries/ffmpeg-macos-x86_64-v7.1',
        'format': 'bestaudio/best',
        'extractaudio': True,
        'audioformat': 'mp3',
        'outtmpl': '/Users/khatri/TubeAgent/runtime/audio/%(title)s.%(ext)s',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
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

        ydl.download([url])


