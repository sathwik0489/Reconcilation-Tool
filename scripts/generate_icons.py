"""Dependency-free raster export of the code-native two-color application mark."""
from pathlib import Path
import struct
import zlib

root = Path(__file__).resolve().parents[1] / 'frontend' / 'public'
def write_icon(size, filename):
    pixels = bytearray(bytes((20,33,61)) * size * size)
    def line(x1,y1,x2,y2):
        # Coordinates in a 128-unit safe area; solid white strokes.
        for i in range(size*2):
            t = i/(size*2-1)
            x = round((x1+(x2-x1)*t)*size/128)
            y = round((y1+(y2-y1)*t)*size/128)
            r = max(1,round(size*2.8/128))
            for dy in range(-r,r+1):
                for dx in range(-r,r+1):
                    xx,yy=x+dx,y+dy
                    if dx*dx+dy*dy<=r*r and 0<=xx<size and 0<=yy<size:
                        off=(yy*size+xx)*3
                        pixels[off:off+3]=b'\xff\xff\xff'
    for a,b in [((32,46),(64,29)),((64,29),(96,46)),((96,46),(64,63)),((64,63),(32,46)),((32,65),(64,82)),((64,82),(96,65)),((32,84),(64,101)),((64,101),(96,84))]:line(*a,*b)
    raw = b''.join(b'\x00'+pixels[y*size*3:(y+1)*size*3] for y in range(size))
    def chunk(name,data):return struct.pack('!I',len(data))+name+data+struct.pack('!I',zlib.crc32(name+data)&0xffffffff)
    (root/filename).write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!IIBBBBB',size,size,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b''))
for size,name in [(192,'icon-192.png'),(512,'icon-512.png'),(180,'apple-touch-icon.png')]:write_icon(size,name)
print('PWA icons generated.')
