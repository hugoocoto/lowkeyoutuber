"""Text-to-speech backends. speak() writes audio next to out_base (any extension) and returns
{"audio": path, "words": [{"w","s","e"}] or None}. Backends without timestamps return None:
tts.py then times the words with a transcribe backend."""
