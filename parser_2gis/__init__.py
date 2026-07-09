from .version import version as __version__


def main(*args, **kwargs):
    from .main import main as _main
    return _main(*args, **kwargs)


__all__ = [
    'main',
    '__version__',
]
