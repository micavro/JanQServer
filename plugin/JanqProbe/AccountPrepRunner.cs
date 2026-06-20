using UnityEngine;

namespace JanqProbe;

internal sealed class AccountPrepRunner : MonoBehaviour
{
    private static AccountPrepRunner? instance;

    public static void Ensure()
    {
        if (instance != null)
        {
            return;
        }

        var host = new GameObject("JanqAccountPrepBridge");
        host.hideFlags = HideFlags.HideAndDontSave;
        DontDestroyOnLoad(host);
        instance = host.AddComponent<AccountPrepRunner>();
    }

    private void Update()
    {
        AccountPrepBridge.Tick();
    }

    private void OnApplicationQuit()
    {
        AccountPrepBridge.Shutdown();
        instance = null;
    }
}
