package com.videodl.app

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * 登录信息（B站登录、导入 / 网页登录得到的网站 cookies）的加解密，给 Python 端的 app/secure_store.py 用。
 *
 * 密钥在 Android Keystore 里生成，只能在这台手机的这个 App 里使用、导不出来；
 * 所以 App 私有目录里的登录文件就算被拷走（root、备份）也解不开。
 * Python 和 Kotlin 之间传字节数组不方便，收发都用 base64 字符串。
 */
@Suppress("unused")     // 由 Python 按名字调用
object SecureStore {
    private const val ALIAS = "videodl_login_v1"
    private const val IV_LEN = 12

    private fun key(): SecretKey {
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (ks.getKey(ALIAS, null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(
                KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .setKeySize(256)
                    .build()
            )
        }.generateKey()
    }

    /** 明文（base64）→ 随机 IV + 密文（base64） */
    @Synchronized
    fun encrypt(plainB64: String): String {
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(Cipher.ENCRYPT_MODE, key())
        val out = c.iv + c.doFinal(Base64.decode(plainB64, Base64.NO_WRAP))
        return Base64.encodeToString(out, Base64.NO_WRAP)
    }

    /** IV + 密文（base64）→ 明文（base64）。密钥不对 / 文件被改过会抛异常，Python 端当作「没有登录信息」处理。 */
    @Synchronized
    fun decrypt(blobB64: String): String {
        val blob = Base64.decode(blobB64, Base64.NO_WRAP)
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, blob, 0, IV_LEN))
        return Base64.encodeToString(c.doFinal(blob, IV_LEN, blob.size - IV_LEN), Base64.NO_WRAP)
    }
}
