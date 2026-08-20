package com.androidagent.client

import android.content.Context
import android.widget.Toast

/** 设计稿中的云端账号能力尚未接入现网时，只做界面展示。 */
object CloudPreview {
    fun show(context: Context) {
        Toast.makeText(context, R.string.cloud_feature_preview, Toast.LENGTH_SHORT).show()
    }
}
