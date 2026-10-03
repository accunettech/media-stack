// Use the hostname (or IP address) the visitor used to open the homepage.
document.querySelectorAll("a[data-port]").forEach((link) => {
  const url = new URL(window.location.href);
  url.protocol = "http:";
  url.port = link.dataset.port;
  url.pathname = "/";
  url.search = "";
  url.hash = "";
  link.href = url.href;
});
