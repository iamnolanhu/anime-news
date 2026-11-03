export const SITE_URL = "https://anime-news.vercel.app";
export const REPO_URL = "https://github.com/iamnolanhu/anime-news";
export const ENGINE_DEFAULT_URL = "http://127.0.0.1:8765";
export const ENGINE_SPEC = `git+${REPO_URL}#subdirectory=packages/engine`;
export const UVX_SERVE = `uvx --from "${ENGINE_SPEC}" anime-news serve`;
export const UVX_MAKE = `uvx --from "${ENGINE_SPEC}" anime-news make "Mayor bans Mondays"`;
export const INSTALL_TOOLS = "brew install uv ffmpeg";
