package com.androidagent.client

import android.content.Context
import android.os.Bundle
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.widget.Toolbar
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.androidagent.client.theokit.TheoComponent
import com.androidagent.client.theokit.TheoType
import com.androidagent.client.theokit.TheokitCatalog
import com.androidagent.client.theokit.theoPalette
import com.androidagent.client.theokit.theoText

/**
 * TheoKit 组件总览 — Android 端 108 个迁移组件的画廊，
 * 对应桌面端 desktop/src/theokit-showcase.html。
 */
class TheokitShowcaseActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val p = theoPalette()
        val root = LinearLayout(this)
        root.orientation = LinearLayout.VERTICAL
        root.setBackgroundColor(p.background)

        val toolbar = Toolbar(this)
        toolbar.setTitle(R.string.theokit_showcase_title)
        toolbar.setTitleTextColor(p.foreground)
        toolbar.setBackgroundColor(p.background)
        setSupportActionBar(toolbar)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)

        val recycler = RecyclerView(this)
        recycler.layoutManager = LinearLayoutManager(this)
        recycler.adapter = TheokitShowcaseAdapter(TheokitCatalog.COMPONENTS)
        recycler.setBackgroundColor(p.background)

        root.addView(
            toolbar,
            LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT),
        )
        root.addView(
            recycler,
            LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f),
        )
        setContentView(root)
    }

    override fun onSupportNavigateUp(): Boolean {
        finish()
        return true
    }

    companion object {
        fun start(context: Context) {
            context.startActivity(android.content.Intent(context, TheokitShowcaseActivity::class.java))
        }
    }
}

class TheokitShowcaseAdapter(private val components: List<TheoComponent>) :
    RecyclerView.Adapter<RecyclerView.ViewHolder>() {

    companion object {
        private const val TYPE_HEADER = 0
        private const val TYPE_DEMO = 1
    }

    private val items: List<Any> = buildList {
        TheokitCatalog.FAMILIES.forEach { family ->
            add(family)
            addAll(components.filter { it.family == family })
        }
    }

    override fun getItemCount(): Int = items.size

    override fun getItemViewType(position: Int): Int =
        if (items[position] is String) TYPE_HEADER else TYPE_DEMO

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): RecyclerView.ViewHolder {
        val ctx = parent.context
        val padV = (4 * ctx.resources.displayMetrics.density).toInt()
        val padH = (5 * ctx.resources.displayMetrics.density).toInt()
        return if (viewType == TYPE_HEADER) {
            val tv = ctx.theoText("", TheoType.TITLE_SM, ctx.theoPalette().foreground)
            tv.setPadding(padH, padV * 2, padH, padV)
            HeaderHolder(tv)
        } else {
            val frame = FrameLayout(ctx)
            frame.setPadding(padH, (2 * ctx.resources.displayMetrics.density).toInt(), padH, (2 * ctx.resources.displayMetrics.density).toInt())
            DemoHolder(frame)
        }
    }

    override fun onBindViewHolder(holder: RecyclerView.ViewHolder, position: Int) {
        val item = items[position]
        when (holder) {
            is HeaderHolder -> (holder.itemView as TextView).text = item as String
            is DemoHolder -> {
                val ctx = holder.itemView.context
                val frame = holder.itemView as FrameLayout
                frame.removeAllViews()
                frame.addView(TheokitCatalog.demoCard(ctx, item as TheoComponent))
            }
        }
    }

    class HeaderHolder(view: View) : RecyclerView.ViewHolder(view)
    class DemoHolder(view: FrameLayout) : RecyclerView.ViewHolder(view)
}
