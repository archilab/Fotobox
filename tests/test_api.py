import io
from PIL import Image
from conftest import process
from photobox_server.core import db_connect
from photobox_server.pool import import_seed

def img_bytes():
    im=Image.new('RGB',(120,160),'#878787')
    out=io.BytesIO(); im.save(out,format='JPEG');return out.getvalue()

def test_flow(client):
    with db_connect() as db:
        import_seed(db,img_bytes(),"SYNTHETIC")
    assert client.get('/api/v1/health').json()['status']=='ok'
    s=client.post('/api/v1/sessions',json={'box_id':'pi-01'}).json()['id']
    denied=client.post(f'/api/v1/sessions/{s}/consent',json={'processing':True,'pool':True,'documented_externally':False})
    assert denied.status_code==403
    assert client.post(f'/api/v1/sessions/{s}/consent',json={'processing':True,'pool':True,'documented_externally':True}).status_code==200
    up=client.post(f'/api/v1/sessions/{s}/portrait',files={'image':('test.jpg',img_bytes(),'image/jpeg')})
    assert up.status_code==200
    result=process(client,s)
    assert result.status_code==202, result.text
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
