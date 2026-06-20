using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using HarmonyLib;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace JanqProbe;

[HarmonyPatch]
internal static class JanqSequenceExitTaskPatch
{
    private static MethodBase TargetMethod()
    {
        var type = AccessTools.TypeByName("Casino.JanqSequence")
            ?? throw new MissingMemberException("Casino.JanqSequence");
        return AccessTools.Method(type, "janqExitTask")
            ?? throw new MissingMethodException(type.FullName, "janqExitTask");
    }

    private static bool Prefix(object __instance, ref IEnumerator __result)
    {
        __result = SafeExitTask(__instance);
        return false;
    }

    private static IEnumerator SafeExitTask(object sequence)
    {
        ProbeLog.Write("janq_exit_task_safe_started", new
        {
            activeScene = SceneManager.GetActiveScene().name,
            loadedScenes = LoadedSceneNames()
        });

        RequestJanqLoadingMask();
        yield return new WaitForSeconds(0.2f);
        yield return UnloadSceneIfLoaded("JanqGameImpl");
        Resources.UnloadUnusedAssets();
        GC.Collect();

        var parent = FieldInHierarchy(sequence.GetType(), "parent")?.GetValue(sequence)
            ?? throw new MissingFieldException(sequence.GetType().FullName, "parent");
        SetField(sequence, "next", CreateBetMenu(parent));

        ProbeLog.Write("janq_exit_task_safe_completed", new
        {
            activeScene = SceneManager.GetActiveScene().name,
            loadedScenes = LoadedSceneNames()
        });
    }

    private static IEnumerator UnloadSceneIfLoaded(string sceneName)
    {
        var scene = SceneManager.GetSceneByName(sceneName);
        if (!scene.IsValid() || !scene.isLoaded)
        {
            ProbeLog.Write("janq_exit_task_unload_skipped", new
            {
                scene = sceneName,
                reason = "scene_not_loaded",
                loadedScenes = LoadedSceneNames()
            });
            yield break;
        }

        if (SceneManager.sceneCount <= 1)
        {
            ProbeLog.Write("janq_exit_task_unload_skipped", new
            {
                scene = sceneName,
                reason = "only_loaded_scene",
                loadedScenes = LoadedSceneNames()
            });
            yield break;
        }

        if (SceneManager.GetActiveScene().name == sceneName)
        {
            for (var index = 0; index < SceneManager.sceneCount; index += 1)
            {
                var candidate = SceneManager.GetSceneAt(index);
                if (candidate.IsValid() && candidate.isLoaded && candidate.name != sceneName)
                {
                    SceneManager.SetActiveScene(candidate);
                    break;
                }
            }
        }

        AsyncOperation? asyncOperation = null;
        Exception? unloadError = null;
        try
        {
            asyncOperation = SceneManager.UnloadSceneAsync(scene);
        }
        catch (Exception ex)
        {
            unloadError = ex;
        }

        if (unloadError != null || asyncOperation == null)
        {
            ProbeLog.Write("janq_exit_task_unload_failed", new
            {
                scene = sceneName,
                error = unloadError?.ToString() ?? "UnloadSceneAsync returned null",
                loadedScenes = LoadedSceneNames()
            });
            yield break;
        }

        while (!asyncOperation.isDone)
        {
            yield return null;
        }

        ProbeLog.Write("janq_exit_task_unloaded_scene", new
        {
            scene = sceneName,
            loadedScenes = LoadedSceneNames()
        });
    }

    private static void RequestJanqLoadingMask()
    {
        var type = AccessTools.TypeByName("Casino.Janq.JanqSequenceManager");
        AccessTools.Method(type, "RequestLoadingMask")?.Invoke(null, null);
    }

    private static object CreateBetMenu(object parent)
    {
        var type = AccessTools.TypeByName("Casino.BetMenu")
            ?? throw new MissingMemberException("Casino.BetMenu");
        foreach (var method in type.GetMethods(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static)
            .Where(candidate => candidate.Name == "Create"))
        {
            var parameters = method.GetParameters();
            if (parameters.Length == 0 || !parameters[0].ParameterType.IsInstanceOfType(parent))
            {
                continue;
            }

            var args = new object?[parameters.Length];
            args[0] = parent;
            for (var index = 1; index < parameters.Length; index += 1)
            {
                if (parameters[index].ParameterType == typeof(bool))
                {
                    args[index] = parameters[index].HasDefaultValue
                        ? parameters[index].DefaultValue
                        : true;
                }
                else
                {
                    args[index] = parameters[index].HasDefaultValue
                        ? parameters[index].DefaultValue
                        : null;
                }
            }

            return method.Invoke(null, args)
                ?? throw new InvalidOperationException("Casino.BetMenu.Create returned null");
        }

        throw new MissingMethodException(type.FullName, "Create");
    }

    private static void SetField(object instance, string name, object? value)
    {
        var field = FieldInHierarchy(instance.GetType(), name)
            ?? throw new MissingFieldException(instance.GetType().FullName, name);
        field.SetValue(instance, value);
    }

    private static FieldInfo? FieldInHierarchy(Type type, string name)
    {
        for (var current = type; current != null; current = current.BaseType)
        {
            var field = current.GetField(name, BindingFlags.Instance | BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly);
            if (field != null)
            {
                return field;
            }
        }
        return null;
    }

    private static string[] LoadedSceneNames()
    {
        var names = new List<string>();
        for (var index = 0; index < SceneManager.sceneCount; index += 1)
        {
            names.Add(SceneManager.GetSceneAt(index).name);
        }
        return names.ToArray();
    }
}
