"""Formato compartido entre la primera pintura (servidor) y el auto-refresh (JS).

El texto de duraciones se arma acá y viaja listo en el snapshot: así la
plantilla y el JavaScript no tienen cada uno su propia versión de "3d 4h".
"""


def format_duration(seconds):
    if seconds is None:
        return None
    seconds = int(seconds)
    if seconds < 0:
        return None
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    if days:
        return f'{days}d {hours}h'
    if hours:
        return f'{hours}h {minutes}m'
    if minutes:
        return f'{minutes}m {secs}s'
    return f'{secs}s'
