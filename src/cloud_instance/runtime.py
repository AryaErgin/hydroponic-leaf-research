"""Strict independent Cloud Run factory; startup requires real CPU inference."""
import os
from ..hydroponic.config import ROOT
from .config import CloudInstanceConfig


def verify_scientific_runtime():
    from ..local_setup.diagnostics import model_smoke
    return model_smoke(ROOT,'cpu')


def build():
    from ..firebase_preview.app import create_app
    from .gateway import IndependentGateway
    if not os.getenv('K_SERVICE'):raise ValueError('Independent cloud runtime requires explicit Cloud Run mode')
    config=CloudInstanceConfig.load(os.environ['HYDRO_CLOUD_INSTANCE_CONFIG'])
    config.guard()
    settings=config.scientific_settings(os.environ['HYDRO_AUTH_TOKEN']).validate()
    # Verify all installed bundle hashes and exercise the actual promoted CPU
    # predictor before opening a socket or creating remote account metadata.
    model=verify_scientific_runtime()
    gateway=IndependentGateway(config.auth_config())
    try:
        app=create_app(gateway.config,gateway,settings.data_dir,scientific_settings=settings)
    except BaseException:
        gateway.close();raise
    app.state.independent_model_status=model
    return app


def main():
    import uvicorn
    # Cloud Run peer addresses are shared. Public forwarding headers cannot be
    # trusted as a source for bounded anonymous-attempt limits. Verified UID
    # limits and explicit HTTPS origin/cookie configuration remain authoritative.
    uvicorn.run(build(),host='0.0.0.0',port=int(os.getenv('PORT','8080')),
        proxy_headers=False,access_log=False)


if __name__=='__main__':main()
