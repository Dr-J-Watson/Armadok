"""Attaques probables, reconnues par signatures dans la ligne de requête du journal d'accès.

Ce sont des INDICES, pas des verdicts : des motifs simples (chemins, fragments d'injection, navigateur annoncé) qui produisent des faux positifs et
ratent ce qu'ils ne connaissent pas. On ne voit ni le corps des requêtes POST ni ce qui est refusé avant d'arriver à Traefik. Les paramètres de
la requête sont LUS ici (une injection s'y cache) mais jamais conservés : on ne garde que le type détecté et un exemple SANS paramètres.

Types détectés à l'écriture (par requête) : fichiers sensibles, balayage d'applications, remontée de répertoires, injection SQL, XSS, exécution
de commandes, outil d'attaque connu, échec de connexion, et « fichier sensible servi » (un fichier sensible qui répond 200 : fuite possible).
Types déduits à la lecture (par adresse) : essai de mots de passe, énumération de chemins, inondation."""
import re
import urllib.parse

# clé -> (libellé, gravité, explication courte)
CATALOG: dict[str, tuple[str, str, str]] = {
    "leak": ("Fichier sensible servi", "critical", "Un fichier qui ne devrait jamais être public (.env, .git…) a répondu 200 : il a peut-être été lu. À vérifier tout de suite."),
    "rce": ("Exécution de commandes probable", "high", "Fragments typiques d'une exécution de code à distance (Log4Shell ${jndi:, /bin/sh, eval…)."),
    "sqli": ("Injection SQL probable", "high", "Fragments typiques d'une injection SQL (union select, ' or 1=1, sleep()…)."),
    "traversal": ("Remontée de répertoires", "high", "Tentative de sortir du dossier du site (../, /etc/passwd…)."),
    "bruteforce": ("Essai de mots de passe", "high", "Beaucoup d'échecs (401 / 403) sur des pages de connexion."),
    "xss": ("Injection de script (XSS) probable", "medium", "Balises ou scripts glissés dans l'adresse (<script, onerror=…)."),
    "sensitive_file": ("Recherche de fichiers sensibles", "medium", "Demande de fichiers comme .env, .git, sauvegardes, wp-config.php."),
    "scanner_ua": ("Outil d'attaque connu", "medium", "Le navigateur annoncé est celui d'un outil de balayage ou d'attaque (sqlmap, nikto, nmap, zgrab…)."),
    "flood": ("Inondation de requêtes", "medium", "Un débit anormal depuis une seule adresse."),
    "cms_probe": ("Balayage d'applications", "low", "Demandes de pages d'administration ou d'applications courantes (WordPress, phpMyAdmin…) qui échouent."),
    "enumeration": ("Énumération de chemins", "low", "Beaucoup de chemins différents demandés et refusés : recherche de ce qui existe."),
}
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
SEVERE = {"leak", "rce", "sqli", "traversal"}        # alerte « tentative d'exploitation » : ce sont des tentatives de prise de contrôle

BRUTE_MIN = 20          # échecs de connexion sur la période (un utilisateur qui se trompe quelques fois ne doit pas déclencher)
ENUM_MIN = 20           # chemins différents en erreur
FLOOD_PER_HOUR = 1500   # requêtes dans une heure

_SENSITIVE = re.compile(
    r"(/\.(env|git|svn|hg|aws|ssh|htpasswd|htaccess|ds_store|npmrc|docker|bash_history|vscode|idea)(\b|/|\.|$)"
    r"|/(id_rsa|wp-config\.php|database\.sql|dump\.sql|backup\.(sql|zip|tar|gz)|phpinfo\.php|server-status|credentials\.json|secrets?\.(json|ya?ml))"
    r"|/\.env\.[a-z0-9_-]+|\.(bak|swp|orig|sql)$)")
_CMS = re.compile(
    r"(wp-login\.php|/wp-admin|/wp-content|/wp-includes|xmlrpc\.php|/phpmyadmin|/pma/|/adminer|/administrator/|/joomla|/drupal|/typo3|/magento|"
    r"/actuator|/boaform|/hnap1|/cgi-bin/|/solr/|/jenkins|/manager/html|/vendor/phpunit|/owa/|/autodiscover|/ecp/|/console/|\.php$)")
_TRAVERSAL = re.compile(r"(\.\./|\.\.\\|%2e%2e|/etc/passwd|/etc/shadow|/proc/self|c:\\windows|boot\.ini|win\.ini)")
_SQLI = re.compile(
    r"(union(\s|/\*.*?\*/)+(all\s+)?select|select\s.+\sfrom\s|'\s*or\s*'?\d'?\s*=\s*'?\d|\bor\s+\d\s*=\s*\d|sleep\(\d+\)|benchmark\(|information_schema|;\s*drop\s+table|waitfor\s+delay|extractvalue\(|updatexml\()")
_XSS = re.compile(r"(<script|</script|javascript:|onerror\s*=|onload\s*=|<svg[^>]*onload|document\.cookie|alert\(\d*\)|<img[^>]+src\s*=)")
_RCE = re.compile(
    r"(\$\{jndi:|\$\{\$\{|/bin/(ba)?sh\b|cmd\.exe|;\s*(wget|curl)\s|\|\s*(sh|bash)\b|\beval\(|base64_decode\(|php://input|allow_url_include|auto_prepend_file|think\\app|\bexec\()")
_UA = re.compile(r"(sqlmap|nikto|nmap|masscan|zgrab|gobuster|dirbuster|\bdirb\b|wfuzz|\bffuf\b|nuclei|acunetix|nessus|openvas|burpsuite|w3af|wpscan|\bhydra\b|metasploit|havij|arachni|zmap|netsparker|jaeles)", re.I)
_LOGIN = re.compile(r"(login|signin|sign-in|/auth|/session|/token|wp-login|xmlrpc|/api/v\d/users/sign)")


def _decode(raw: str) -> str:
    s = raw
    for _ in range(2):   # les attaques doublent souvent l'encodage (%252e)
        try:
            n = urllib.parse.unquote(s)
        except Exception:  # noqa: BLE001
            break
        if n == s:
            break
        s = n
    return s.replace("+", " ").lower()[:2000]


def sample_of(host: str, path: str) -> str:
    """Exemple affichable : site + chemin SANS paramètres (ils peuvent contenir des jetons)."""
    return (host + (path.split("?", 1)[0] or "/"))[:120]


def classify(raw: str, ua: str, status: int, path: str) -> list[str]:
    """Types d'attaque probables d'UNE requête. `raw` : chemin avec paramètres éventuels ; `path` : chemin seul."""
    s = _decode(raw)
    p = _decode(path.split("?", 1)[0])
    out = []
    if _TRAVERSAL.search(s):
        out.append("traversal")
    if _SQLI.search(s):
        out.append("sqli")
    if _XSS.search(s):
        out.append("xss")
    if _RCE.search(s):
        out.append("rce")
    if _SENSITIVE.search(p):
        out.append("sensitive_file")
        if status in (200, 206):
            out.append("leak")
    if _CMS.search(p) and 400 <= status < 500:     # une page d'administration qui répond 200, c'est peut-être la vôtre : pas une attaque
        out.append("cms_probe")
    if ua and _UA.search(ua):
        out.append("scanner_ua")
    if status in (401, 403) and _LOGIN.search(p):
        out.append("login_fail")
    return out


def derive(counts: dict[str, int], error_paths: int, max_hour: int) -> dict[str, int]:
    """Types déduits de compteurs : {clé: valeur qui l'a déclenché}."""
    d = {}
    if counts.get("login_fail", 0) >= BRUTE_MIN:
        d["bruteforce"] = counts["login_fail"]
    if error_paths >= ENUM_MIN:
        d["enumeration"] = error_paths
    if max_hour >= FLOOD_PER_HOUR:
        d["flood"] = max_hour
    return d


def describe(kind: str) -> dict:
    label, sev, why = CATALOG[kind]
    return {"kind": kind, "label": label, "severity": sev, "why": why}
