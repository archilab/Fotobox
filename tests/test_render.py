from PIL import Image
from photobox_server.core import render_mock_strip

def test_three_and_four_frames():
    a=Image.new('RGB',(100,120),'white');b=Image.new('RGB',(100,120),'black')
    for n in (3,4):
        result=render_mock_strip(a,b,n)
        assert result.width==560 and result.height>n*400
