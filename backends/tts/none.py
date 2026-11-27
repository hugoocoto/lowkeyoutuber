"""No TTS: the narration is recorded by a person. speak() stops the flow and says so."""

from backends import Base, NeedsHuman


class Backend(Base):
    caps = {"local": True, "timestamps": False, "person": True}
    model = "you"

    def cache_id(self, draft=False):
        return ["none"]

    def speak(self, text, out_base, lang=None, context=None, draft=False):
        raise NeedsHuman("TTS is set to 'none': record this line yourself, then run align.py "
                         "to match the recording to the script")
