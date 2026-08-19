from config import config as c
s = c.smtp
p = s.password
print("USER :", repr(s.user))
print("PASS : uzunluk", len(p), "| bosluksuz", len(p.replace(" ", "")))
print("PASS : ilk/son karakter", repr(p[:1]), repr(p[-1:]))
print("TO   :", s.mail_to)
print("HOST :", s.host, s.port, "| TLS", s.use_tls, "| SSL", s.use_ssl)
print("EKSIK:", s.missing_fields())
