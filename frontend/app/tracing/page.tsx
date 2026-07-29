// Use the same-origin `/zipkin` rewrite (next.config.js -> ZIPKIN_URL) so the
// iframe works on remote deployments where Zipkin is not on the viewer's host.
const TracingPage = () => (
    <iframe src="/zipkin" className="iframe" style={{ width: "100%", height: "calc(100vh - 129px)" }} />
)

export default TracingPage;
