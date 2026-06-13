using HarmonyLib;
using UnityEngine;

namespace JanqProbe;

internal sealed class ActionBridgeRunner : MonoBehaviour
{
    private static ActionBridgeRunner? instance;

    public static void Ensure()
    {
        if (instance != null)
        {
            return;
        }

        var host = new GameObject("JanqActionBridge");
        host.hideFlags = HideFlags.HideAndDontSave;
        DontDestroyOnLoad(host);
        instance = host.AddComponent<ActionBridgeRunner>();
    }

    private void Update()
    {
        JanqNavigator.Tick();
        ActionBridge.Tick();
    }

    private void OnApplicationQuit()
    {
        ActionBridge.Shutdown();
        new Harmony("janq.lab.probe").UnpatchSelf();
        ProbeLog.Write("probe_unloaded", new { });
        instance = null;
    }
}
