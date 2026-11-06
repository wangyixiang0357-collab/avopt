"""云端入口核验：访问保护、长度限制与原计算路由代理。"""
import base64
import io
import json
import unittest
from cloud_wsgi import CloudGateway


class CloudTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gateway = CloudGateway("test-only-password-2026")
        cls.addClassCleanup(cls.gateway.close)

    def call(self,path="/",auth=True,method="GET",body=b"",length=None,origin=None):
        env={"PATH_INFO":path,"REQUEST_METHOD":method,"QUERY_STRING":"", "CONTENT_LENGTH":str(len(body) if length is None else length),"CONTENT_TYPE":"application/json","wsgi.input":io.BytesIO(body)}
        if auth:
            token=base64.b64encode(b"avopt:test-only-password-2026").decode()
            env["HTTP_AUTHORIZATION"]="Basic "+token
        if origin is not None:
            env['HTTP_ORIGIN']=origin
            env['HTTP_HOST']='example.onrender.com'
        result={}
        def start(status,headers):result.update(status=status,headers=dict(headers))
        result['body']=b''.join(self.gateway(env,start))
        return result

    def test_requires_password_for_page_and_api(self):
        for path in ('/','/api/weather/years','/api/radiation/import'):
            r=self.call(path,auth=False)
            self.assertTrue(r['status'].startswith('401'))
            self.assertIn('WWW-Authenticate',r['headers'])

    def test_public_health_and_authenticated_page(self):
        r=self.call('/api/health',auth=False)
        self.assertTrue(r['status'].startswith('200'))
        self.assertTrue(json.loads(r['body'])['ok'])
        page=self.call('/')
        self.assertIn(b'ERA5',page['body'])
        self.assertEqual(page['headers']['Content-Type'],'text/html; charset=utf-8')

    def test_original_api_and_error_status_preserved(self):
        r=self.call('/api/weather/years')
        self.assertEqual([x['year'] for x in json.loads(r['body'])['years']],list(range(2020,2027)))
        bad=self.call('/api/run',method='POST',body=b'{"lat":"nan"}')
        self.assertTrue(bad['status'].startswith('400'))
        self.assertFalse(json.loads(bad['body'])['ok'])

    def test_limits_and_unsupported_methods(self):
        self.assertTrue(self.call('/api/run',method='POST',length=8000001)['status'].startswith('413'))
        self.assertTrue(self.call('/',method='DELETE')['status'].startswith('405'))

    def test_cross_site_write_is_rejected(self):
        r=self.call('/api/run',method='POST',body=b'{"lat":"nan"}',origin='https://unrelated.example')
        self.assertTrue(r['status'].startswith('403'))
        own=self.call('/api/run',method='POST',body=b'{"lat":"nan"}',origin='https://example.onrender.com')
        self.assertTrue(own['status'].startswith('400'))

    def test_rejects_missing_password_and_invalid_auth(self):
        for value in ('',None,'123'):
            with self.assertRaises(ValueError):CloudGateway(value)
        for header in ('','Bearer x','Basic broken','Basic !!!', 'Basic '+base64.b64encode(b'avopt:wrong-password').decode()):
            self.assertFalse(self.gateway._authorized(header))


if __name__=='__main__':unittest.main(verbosity=2)
