"""
DocxToPdfConverter - Converts Word documents (.docx) to PDF files.
This conversion is FREE (no payment required).
"""
import logging
import os
import shutil
import subprocess
import tempfile


logger = logging.getLogger(__name__)


class DocxToPdfConverter:
    """Convert Word documents to PDF using LibreOffice's layout renderer."""

    def __init__(self, docx_path: str, output_path: str):
        self.docx_path = docx_path
        self.output_path = output_path
        self.error_message = None

    def convert(self) -> bool:
        """Convert a .docx file to PDF. Returns True if successful."""
        try:
            return self._convert_with_libreoffice()
        except subprocess.TimeoutExpired:
            self.error_message = 'LibreOffice took too long to convert this document. Please try again.'
            logger.exception('LibreOffice timed out converting DOCX: %s', self.docx_path)
        except Exception as exc:
            self.error_message = (
                'LibreOffice could not convert this document. Check that the DOCX file is valid and try again.'
            )
            logger.exception('Error converting DOCX to PDF: %s', exc)
        return False

    def _convert_with_libreoffice(self) -> bool:
        """Use LibreOffice's document renderer to preserve Word layout."""
        executable = shutil.which('soffice') or shutil.which('libreoffice')
        if not executable:
            self.error_message = (
                'Layout-preserving Word-to-PDF conversion requires LibreOffice. '
                'Please contact the site administrator to install it.'
            )
            return False

        output_dir = os.path.dirname(os.path.abspath(self.output_path))
        os.makedirs(output_dir, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='docx_to_pdf_') as temp_dir:
            profile_uri = (os.path.join(temp_dir, 'profile')).replace(os.sep, '/')
            result_dir = os.path.join(temp_dir, 'output')
            os.makedirs(result_dir)
            command = [
                executable,
                '--headless',
                f'-env:UserInstallation=file:///{profile_uri.lstrip("/")}',
                '--convert-to',
                'pdf',
                '--outdir',
                result_dir,
                os.path.abspath(self.docx_path),
            ]
            result = subprocess.run(command, capture_output=True, text=True, timeout=180)
            rendered_pdf = os.path.join(
                result_dir,
                os.path.splitext(os.path.basename(self.docx_path))[0] + '.pdf',
            )
            if result.returncode != 0 or not os.path.isfile(rendered_pdf):
                logger.warning(
                    'LibreOffice DOCX rendering failed (exit %s): %s',
                    result.returncode,
                    result.stderr.strip() or result.stdout.strip(),
                )
                self.error_message = (
                    'LibreOffice could not render this document. Check that the DOCX file is valid and try again.'
                )
                return False

            shutil.copyfile(rendered_pdf, self.output_path)
            return True
