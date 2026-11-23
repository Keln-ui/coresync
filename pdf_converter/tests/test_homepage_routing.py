from django.test import SimpleTestCase, override_settings


@override_settings(
    ACADEMIC_HOST='academic.test',
    CONVERTER_HOST='converter.test',
    SITE_SCHEME='https',
)
class HomepageRoutingTests(SimpleTestCase):
    def test_local_hosts_show_converter_homepage(self):
        for host in ('academic.test', 'converter.test', '127.0.0.1'):
            with self.subTest(host=host):
                response = self.client.get('/', HTTP_HOST=host)

                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, 'index.html')
                self.assertContains(response, 'Document Converter')
                self.assertContains(response, 'PDF Utilities')
                self.assertNotContains(response, 'Academic Platform')
                self.assertNotContains(response, 'Revision Materials')
                self.assertNotContains(response, 'Teaching Jobs')
