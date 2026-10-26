pub(crate) mod account;
pub(crate) mod catalogue;
pub(crate) mod guild;
pub(crate) mod layout;
pub(crate) mod site;

pub(crate) use account::*;
pub(crate) use catalogue::*;
pub(crate) use guild::*;
pub(crate) use layout::*;
pub(crate) use site::*;

/// A Discord CDN URL at `size` px. Percy hands over discord.py's `Asset.url`, which already
/// carries `?size=1024`; appending a second `?size=` makes the CDN reject the request.
pub(crate) fn cdn_sized(url: impl AsRef<str>, size: u32) -> String {
    let url = url.as_ref();
    let base = url.split_once('?').map_or(url, |(base, _)| base);
    format!("{base}?size={size}")
}

#[cfg(test)]
mod tests {
    #[test]
    fn cdn_sized_replaces_existing_size() {
        let a = "https://cdn.discordapp.com/avatars/1/abc.png";
        assert_eq!(super::cdn_sized(format!("{a}?size=1024"), 128), format!("{a}?size=128"));
        assert_eq!(super::cdn_sized(a, 64), format!("{a}?size=64"));
    }
}
