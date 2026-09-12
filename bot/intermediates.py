"""Bounded parsing of observed intermediate-page data; never evaluates scripts."""
import base64,codecs,json,re
from .models import FlowError


def greenmount_target(text):
    match=re.search(r"\bs\(['\"]o['\"],\s*['\"]([A-Za-z0-9+/=]+)['\"]",text)
    if not match:return None
    try:
        value=base64.b64decode(base64.b64decode(match[1],validate=True),validate=True).decode()
        value=base64.b64decode(codecs.decode(value,'rot_13'),validate=True).decode()
        return base64.b64decode(json.loads(value)['o'],validate=True).decode()
    except (ValueError,KeyError,UnicodeError,TypeError):
        raise FlowError('PARSER_CHANGED','Intermediate redirect data format changed')
def vcloud_target(html):
    """Decode only the observed literal assignment, never execute page scripts."""
    import re, base64, binascii
    match=re.search(r"\bvar\s+url\s*=\s*atob\(atob\(['\"]([A-Za-z0-9+/=]{1,16000})['\"]\)\)",html)
    if not match:return None
    try:return base64.b64decode(base64.b64decode(match[1],validate=True),validate=True).decode('utf-8')
    except (ValueError,UnicodeDecodeError,binascii.Error):return None
