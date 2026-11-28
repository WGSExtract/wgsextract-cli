use std::{ffi::c_void, sync::mpsc, time::Duration};

use block2::{Block, RcBlock};
use dispatch2::DispatchQueue;

type NetworkObject = *mut c_void;

#[link(name = "Network", kind = "framework")]
extern "C" {
    fn nw_path_monitor_create() -> NetworkObject;
    fn nw_path_monitor_set_queue(monitor: NetworkObject, queue: &DispatchQueue);
    fn nw_path_monitor_set_update_handler(
        monitor: NetworkObject,
        handler: &Block<dyn Fn(NetworkObject)>,
    );
    fn nw_path_monitor_start(monitor: NetworkObject);
    fn nw_path_monitor_cancel(monitor: NetworkObject);
    fn nw_path_get_status(path: NetworkObject) -> u32;
    fn nw_path_is_constrained(path: NetworkObject) -> bool;
    fn nw_path_is_expensive(path: NetworkObject) -> bool;
    fn nw_release(object: NetworkObject);
}

pub(crate) fn print_snapshot() {
    if let Some((constrained, expensive)) = snapshot() {
        println!("{constrained} {expensive}");
    }
}

fn snapshot() -> Option<(bool, bool)> {
    let (send, receive) = mpsc::channel();
    let queue = DispatchQueue::new("org.wgse.network-cost", None);
    // Network.framework retains the queue and copies the block; the path is
    // borrowed only for the callback. The monitor observes routes, not traffic.
    let handler = RcBlock::new(move |path: NetworkObject| unsafe {
        let satisfied = nw_path_get_status(path) == 1; // nw_path_status_satisfied
        let _ = send
            .send(satisfied.then(|| (nw_path_is_constrained(path), nw_path_is_expensive(path))));
    });
    unsafe {
        let monitor = nw_path_monitor_create();
        if monitor.is_null() {
            return None;
        }
        nw_path_monitor_set_queue(monitor, &queue);
        nw_path_monitor_set_update_handler(monitor, &handler);
        nw_path_monitor_start(monitor);
        let cost = receive.recv_timeout(Duration::from_secs(1)).ok().flatten();
        nw_path_monitor_cancel(monitor);
        nw_release(monitor);
        cost
    }
}
