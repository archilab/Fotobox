import io
from PIL import Image
from photobox_server.core import storage_root

def img_bytes():
    im=Image.new('RGB',(120,160),'#878787')
    out=io.BytesIO(); im.save(out,format='JPEG');return out.getvalue()

def test_flow(client):
    seed=storage_root()/"seed_portraits"/"seed.png"
    Image.new('RGB',(120,160),'#444444').save(seed)
    assert client.get('/api/v1/health').json()['status']=='ok'
    s=client.post('/api/v1/sessions',json={'box_id':'pi-01'}).json()['id']
    denied=client.post(f'/api/v1/sessions/{s}/consent',json={'processing':True,'pool':True,'documented_externally':False})
    assert denied.status_code==403
    assert client.post(f'/api/v1/sessions/{s}/consent',json={'processing':True,'pool':True,'documented_externally':True}).status_code==200
    up=client.post(f'/api/v1/sessions/{s}/portrait',files={'image':('test.jpg',img_bytes(),'image/jpeg')})
    assert up.status_code==200
    result=client.post(f'/api/v1/sessions/{s}/process')
    assert result.status_code==200, result.text
    assert client.get(f'/api/v1/sessions/{s}').json()['status']=='complete'
    img=client.get(f'/api/v1/sessions/{s}/result')
    assert img.status_code==200 and img.headers['content-type']=='image/png'
    assert len(img.content)>1000

def test_cannot_upload_without_consent(client):
    sid=client.post('/api/v1/sessions',json={}).json()['id']
    r=client.post(f'/api/v1/sessions/{sid}/portrait',files={'image':('x.jpg',img_bytes(),'image/jpeg')})
    assert r.status_code==409

def test_requires_seed(client):
    sid=client.post('/api/v1/sessions',json={}).json()['id']
    client.post(f'/api/v1/sessions/{sid}/consent',json={'processing':True,'documented_externally':True})
    client.post(f'/api/v1/sessions/{sid}/portrait',files={'image':('x.jpg',img_bytes(),'image/jpeg')})
    assert client.post(f'/api/v1/sessions/{sid}/process').status_code==409
