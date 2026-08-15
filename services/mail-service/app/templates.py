"""Plantilla del correo de verificación de cuenta (CHG-043).

El token de verificación viaja únicamente dentro del cuerpo del correo
(ADR-004): jamás en logs ni en respuestas HTTP. El HTML usa solo CSS en
línea porque los clientes de correo no cargan hojas externas.
"""

from dataclasses import dataclass
from html import escape


@dataclass(frozen=True)
class RenderedEmail:
    subject: str
    text_body: str
    html_body: str


def verification_link(public_base_url: str, token: str) -> str:
    return f"{public_base_url}/verificar-correo?token={token}"


def render_verification_email(
    public_base_url: str, token: str, expires_hours: int
) -> RenderedEmail:
    link = verification_link(public_base_url, token)
    subject = "Confirma tu correo — Plataforma CUSOL Desastres"

    text_body = (
        "Hola:\n"
        "\n"
        "Gracias por unirte a la Plataforma CUSOL de respuesta a "
        "desastres.\n"
        "Cada cuenta verificada fortalece la red que ayuda a reunir "
        "familias,\n"
        "reportar personas desaparecidas y orientar la ayuda "
        "humanitaria en Colombia.\n"
        "\n"
        "Para activar tu cuenta abre este enlace (vence en "
        f"{expires_hours} horas):\n"
        "\n"
        f"{link}\n"
        "\n"
        "Si el enlace no funciona, copia este código de verificación "
        "en la página\n"
        "de confirmación:\n"
        "\n"
        f"{token}\n"
        "\n"
        "¿No creaste esta cuenta? Ignora este mensaje y no se activará "
        "nada.\n"
        "\n"
        "Con gratitud,\n"
        "Equipo CUSOL — Monitoreo Digital Humanitario\n"
    )

    safe_link = escape(link, quote=True)
    safe_token = escape(token)
    html_body = f"""\
<div style="margin:0;padding:24px;background-color:#0f172a;font-family:Arial,Helvetica,sans-serif;">
  <div style="max-width:560px;margin:0 auto;background-color:#ffffff;border-radius:12px;overflow:hidden;">
    <div style="background-color:#b91c1c;padding:20px 32px;">
      <p style="margin:0;color:#ffffff;font-size:20px;font-weight:bold;">CUSOL — Plataforma de Desastres</p>
      <p style="margin:4px 0 0;color:#fecaca;font-size:13px;">Monitoreo Digital Humanitario · Colombia</p>
    </div>
    <div style="padding:32px;">
      <p style="margin:0 0 16px;color:#0f172a;font-size:16px;">Hola:</p>
      <p style="margin:0 0 16px;color:#334155;font-size:15px;line-height:1.6;">
        Gracias por unirte a la red CUSOL. Cada cuenta verificada fortalece
        la comunidad que ayuda a <strong>reunir familias</strong>, reportar
        personas desaparecidas y orientar la ayuda humanitaria cuando más
        se necesita.
      </p>
      <p style="margin:0 0 24px;color:#334155;font-size:15px;line-height:1.6;">
        Confirma que este correo es tuyo para activar tu cuenta. El enlace
        vence en <strong>{expires_hours} horas</strong>.
      </p>
      <div style="text-align:center;margin:0 0 24px;">
        <a href="{safe_link}"
           style="display:inline-block;background-color:#b91c1c;color:#ffffff;text-decoration:none;font-size:16px;font-weight:bold;padding:14px 32px;border-radius:8px;">
          Confirmar mi correo
        </a>
      </div>
      <p style="margin:0 0 8px;color:#64748b;font-size:13px;line-height:1.6;">
        Si el botón no funciona, copia este código en la página de
        confirmación:
      </p>
      <p style="margin:0 0 24px;padding:12px;background-color:#f1f5f9;border-radius:8px;color:#0f172a;font-size:14px;font-family:Consolas,Monaco,monospace;word-break:break-all;">
        {safe_token}
      </p>
      <p style="margin:0;color:#94a3b8;font-size:12px;line-height:1.6;">
        ¿No creaste esta cuenta? Ignora este mensaje y no se activará nada.
      </p>
    </div>
    <div style="padding:16px 32px;background-color:#f8fafc;border-top:1px solid #e2e8f0;">
      <p style="margin:0;color:#94a3b8;font-size:12px;">
        Equipo CUSOL · Universidad Industrial de Santander ·
        <a href="{escape(public_base_url, quote=True)}" style="color:#b91c1c;">cusoldisasterplatform.com</a>
      </p>
    </div>
  </div>
</div>
"""
    return RenderedEmail(
        subject=subject, text_body=text_body, html_body=html_body
    )
